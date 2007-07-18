# (Be in -*- python -*- mode.)
#
# ====================================================================
# Copyright (c) 2006-2007 CollabNet.  All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.  The terms
# are also available at http://subversion.tigris.org/license-1.html.
# If newer versions of this license are posted there, you may use a
# newer version instead, at your option.
#
# This software consists of voluntary contributions made by many
# individuals.  For exact contribution history, see the revision
# history and logs, available at http://cvs2svn.tigris.org/.
# ====================================================================

"""This module contains a class to manage the CVSItems related to one file."""


from __future__ import generators

import re

from cvs2svn_lib.boolean import *
from cvs2svn_lib.set_support import *
from cvs2svn_lib.common import InternalError
from cvs2svn_lib.common import FatalError
from cvs2svn_lib.context import Ctx
from cvs2svn_lib.log import Log
from cvs2svn_lib.symbol import Trunk
from cvs2svn_lib.symbol import Branch
from cvs2svn_lib.symbol import Tag
from cvs2svn_lib.symbol import ExcludedSymbol
from cvs2svn_lib.cvs_item import CVSRevision
from cvs2svn_lib.cvs_item import CVSRevisionModification
from cvs2svn_lib.cvs_item import CVSRevisionAbsent
from cvs2svn_lib.cvs_item import CVSRevisionNoop
from cvs2svn_lib.cvs_item import CVSSymbol
from cvs2svn_lib.cvs_item import CVSBranch
from cvs2svn_lib.cvs_item import CVSTag
from cvs2svn_lib.cvs_item import cvs_revision_type_map
from cvs2svn_lib.cvs_item import cvs_branch_type_map
from cvs2svn_lib.cvs_item import cvs_tag_type_map


class LODItems(object):
  def __init__(self, lod, cvs_branch, cvs_revisions, cvs_branches, cvs_tags):
    # The LineOfDevelopment described by this instance.
    self.lod = lod

    # The CVSBranch starting this LOD, if any; otherwise, None.
    self.cvs_branch = cvs_branch

    # The list of CVSRevisions on this LOD, if any.  The CVSRevisions
    # are listed in dependency order.
    self.cvs_revisions = cvs_revisions

    # A list of CVSBranches that sprout from this LOD (either from
    # cvs_branch or from one of the CVSRevisions).
    self.cvs_branches = cvs_branches

    # A list of CVSTags that sprout from this LOD (either from
    # cvs_branch or from one of the CVSRevisions).
    self.cvs_tags = cvs_tags


class CVSFileItems(object):
  def __init__(self, cvs_file, trunk, cvs_items):
    # The file whose data this instance holds.
    self.cvs_file = cvs_file

    # The symbol that represents "Trunk" in this file.
    self.trunk = trunk

    # A map from CVSItem.id to CVSItem:
    self._cvs_items = {}

    # The cvs_item_id of each root in the CVSItem forest.  (A root is
    # defined to be any CVSRevision with no prev_id.)
    self.root_ids = set()

    for cvs_item in cvs_items:
      self.add(cvs_item)
      if isinstance(cvs_item, CVSRevision) and cvs_item.prev_id is None:
        self.root_ids.add(cvs_item.id)

  def __getstate__(self):
    return (self.cvs_file.id, self.trunk.id, self.values(),)

  def __setstate__(self, state):
    (cvs_file_id, trunk_id, cvs_items,) = state
    CVSFileItems.__init__(
        self, Ctx()._cvs_file_db.get_file(cvs_file_id),
        Ctx()._symbol_db.get_symbol(trunk_id), cvs_items,
        )

  def add(self, cvs_item):
    self._cvs_items[cvs_item.id] = cvs_item

  def __getitem__(self, id):
    """Return the CVSItem with the specified ID."""

    return self._cvs_items[id]

  def __delitem__(self, id):
    assert id not in self.root_ids
    del self._cvs_items[id]

  def values(self):
    return self._cvs_items.values()

  def get_lod_items(self, cvs_branch):
    """Return an LODItems describing the branch that starts at CVS_BRANCH.

    CVS_BRANCH must be an instance of CVSBranch contained in this
    CVSFileItems."""

    cvs_revisions = []
    cvs_branches = []
    cvs_tags = []

    def process_subitems(cvs_item):
      """Process the branches and tags that are rooted in CVS_ITEM.

      CVS_ITEM can be a CVSRevision or a CVSBranch."""

      for branch_id in cvs_item.branch_ids[:]:
        cvs_branches.append(self[branch_id])

      for tag_id in cvs_item.tag_ids:
        cvs_tags.append(self[tag_id])

    # Include the symbols sprouting directly from the CVSBranch:
    process_subitems(cvs_branch)

    id = cvs_branch.next_id
    while id is not None:
      cvs_rev = self[id]
      cvs_revisions.append(cvs_rev)
      process_subitems(cvs_rev)
      id = cvs_rev.next_id

    return LODItems(
        cvs_branch.symbol, cvs_branch, cvs_revisions, cvs_branches, cvs_tags
        )

  def _iter_tree(self, lod, cvs_branch, start_id):
    """Iterate over the tree that starts at the specified line of development.

    LOD is the LineOfDevelopment where the iteration should start.
    CVS_BRANCH is the CVSBranch instance that starts the LOD if any;
    otherwise it is None.  ID is the id of the first CVSRevision on
    this LOD, or None if there are none.

    There are two cases handled by this routine: trunk (where LOD is a
    Trunk instance, CVS_BRANCH is None, and ID is the id of the 1.1
    revision) and a branch (where LOD is a Branch instance, CVS_BRANCH
    is a CVSBranch instance, and ID is either the id of the first
    CVSRevision on the branch or None if there are no CVSRevisions on
    the branch).  Note that CVS_BRANCH and ID cannot simultaneously be
    None.

    Yield an LODItems instance for each line of development."""

    cvs_revisions = []
    cvs_branches = []
    cvs_tags = []

    def process_subitems(cvs_item):
      """Process the branches and tags that are rooted in CVS_ITEM.

      CVS_ITEM can be a CVSRevision or a CVSBranch."""

      for branch_id in cvs_item.branch_ids[:]:
        # Recurse into the branch:
        branch = self[branch_id]
        for lod_items in self._iter_tree(
              branch.symbol, branch, branch.next_id
              ):
          yield lod_items
        # The caller might have deleted the branch that we just
        # yielded.  If it is no longer present, then do not add it to
        # the list of cvs_branches.
        try:
          cvs_branches.append(self[branch_id])
        except KeyError:
          pass

      for tag_id in cvs_item.tag_ids:
        cvs_tags.append(self[tag_id])

    if cvs_branch is not None:
      # Include the symbols sprouting directly from the CVSBranch:
      for lod_items in process_subitems(cvs_branch):
        yield lod_items

    id = start_id
    while id is not None:
      cvs_rev = self[id]
      cvs_revisions.append(cvs_rev)

      for lod_items in process_subitems(cvs_rev):
        yield lod_items

      id = cvs_rev.next_id

    yield LODItems(lod, cvs_branch, cvs_revisions, cvs_branches, cvs_tags)

  def iter_lods(self):
    """Iterate over LinesOfDevelopment in this file, in depth-first order.

    For each LOD, yield an LODItems instance.  The traversal starts at
    each root node but returns the LODs in depth-first order.

    It is allowed to modify the CVSFileItems instance while the
    traversal is occurring, but only in ways that don't affect the
    tree structure above (i.e., towards the trunk from) the current
    LOD."""

    # Make a list out of root_ids so that callers can change it:
    for id in list(self.root_ids):
      cvs_item = self[id]
      if isinstance(cvs_item, CVSRevision):
        # This LOD doesn't have a CVSBranch associated with it.
        # Either it is Trunk, or it is a branch whose CVSBranch has
        # been deleted.
        lod = cvs_item.lod
        cvs_branch = None
      elif isinstance(cvs_item, CVSBranch):
        # This is a Branch that has been severed from the rest of the
        # tree.
        lod = cvs_item.symbol
        id = cvs_item.next_id
        cvs_branch = cvs_item
      else:
        raise InternalError('Unexpected root item: %s' % (cvs_item,))

      for lod_items in self._iter_tree(lod, cvs_branch, id):
        yield lod_items

  def adjust_ntdbrs(self, file_imported, ntdbr_ids, rev_1_2_id):
    """Adjust the non-trunk default branch revisions listed in NTDBR_IDS.

    FILE_IMPORTED is a boolean indicating whether this file appears to
    have been imported, which also means that revision 1.1 has a
    generated log message that need not be preserved.  NTDBR_IDS is a
    list of cvs_rev_ids for the revisions that have been determined to
    be non-trunk default branch revisions.

    The first revision on the default branch is handled strangely by
    CVS.  If a file is imported (as opposed to being added), CVS
    creates a 1.1 revision, then creates a vendor branch 1.1.1 based
    on 1.1, then creates a 1.1.1.1 revision that is identical to the
    1.1 revision (i.e., its deltatext is empty).  The log message that
    the user typed when importing is stored with the 1.1.1.1 revision.
    The 1.1 revision always contains a standard, generated log
    message, 'Initial revision\n'.

    When we detect a straightforward import like this, we want to
    handle it by deleting the 1.1 revision (which doesn't contain any
    useful information) and making 1.1.1.1 into an independent root in
    the file's dependency tree.  In SVN, 1.1.1.1 will be added
    directly to the vendor branch with its initial content.  Then in a
    special 'post-commit', the 1.1.1.1 revision is copied back to
    trunk.

    If the user imports again to the same vendor branch, then CVS
    creates revisions 1.1.1.2, 1.1.1.3, etc. on the vendor branch,
    *without* counterparts in trunk (even though these revisions
    effectively play the role of trunk revisions).  So after we add
    such revisions to the vendor branch, we also copy them back to
    trunk in post-commits.

    Set the default_branch_revision members of the revisions listed in
    NTDBR_IDS to True.  Also, if REV_1_2_ID is not None, then it is
    the id of revision 1.2.  Set that revision to depend on the last
    non-trunk default branch revision and possibly adjust its type
    accordingly."""

    cvs_rev = self[ntdbr_ids[0]]

    if file_imported \
           and cvs_rev.rev == '1.1.1.1' \
           and isinstance(cvs_rev, CVSRevisionModification) \
           and not cvs_rev.deltatext_exists:
      rev_1_1 = self[cvs_rev.prev_id]
      Log().debug('Removing unnecessary revision %s' % (rev_1_1,))

      # Delete rev_1_1:
      self.root_ids.remove(rev_1_1.id)
      del self[rev_1_1.id]
      cvs_rev.prev_id = None
      if rev_1_2_id is not None:
        rev_1_2 = self[rev_1_2_id]
        rev_1_2.prev_id = None
        self.root_ids.add(rev_1_2.id)

      # Delete the 1.1.1 CVSBranch:
      assert cvs_rev.first_on_branch_id is not None
      cvs_branch = self[cvs_rev.first_on_branch_id]
      if cvs_branch.source_id == rev_1_1.id:
        del self[cvs_branch.id]
        rev_1_1.branch_ids.remove(cvs_branch.id)
        rev_1_1.branch_commit_ids.remove(cvs_rev.id)
        cvs_rev.first_on_branch_id = None
        self.root_ids.add(cvs_rev.id)

      # Change the type of cvs_rev (typically from Change to Add):
      cvs_rev.__class__ = cvs_revision_type_map[(
          isinstance(cvs_rev, CVSRevisionModification),
          False,
          )]

      # Move any tags and branches from rev_1_1 to cvs_rev:
      cvs_rev.tag_ids.extend(rev_1_1.tag_ids)
      for id in rev_1_1.tag_ids:
        cvs_tag = self[id]
        cvs_tag.source_lod = cvs_rev.lod
        cvs_tag.source_id = cvs_rev.id
      cvs_rev.branch_ids[0:0] = rev_1_1.branch_ids
      for id in rev_1_1.branch_ids:
        cvs_branch = self[id]
        cvs_branch.source_lod = cvs_rev.lod
        cvs_branch.source_id = cvs_rev.id
      cvs_rev.branch_commit_ids[0:0] = rev_1_1.branch_commit_ids
      for id in rev_1_1.branch_commit_ids:
        cvs_rev2 = self[id]
        cvs_rev2.prev_id = cvs_rev.id

    for cvs_rev_id in ntdbr_ids:
      cvs_rev = self[cvs_rev_id]
      cvs_rev.default_branch_revision = True

    if rev_1_2_id is not None:
      # Revision 1.2 logically follows the imported revisions, not
      # 1.1.  Accordingly, connect it to the last NTDBR and possibly
      # change its type.
      rev_1_2 = self[rev_1_2_id]
      last_ntdbr = self[ntdbr_ids[-1]]
      rev_1_2.default_branch_prev_id = last_ntdbr.id
      last_ntdbr.default_branch_next_id = rev_1_2.id
      rev_1_2.__class__ = cvs_revision_type_map[(
          isinstance(rev_1_2, CVSRevisionModification),
          isinstance(last_ntdbr, CVSRevisionModification),
          )]

  def _delete_unneeded(self, cvs_item, metadata_db):
    if isinstance(cvs_item, CVSRevisionNoop) \
           and cvs_item.rev == '1.1' \
           and isinstance(cvs_item.lod, Trunk) \
           and len(cvs_item.branch_ids) >= 1 \
           and self[cvs_item.branch_ids[0]].next_id is not None \
           and not cvs_item.closed_symbols \
           and not cvs_item.default_branch_revision:
      # FIXME: This message will not match if the RCS file was renamed
      # manually after it was created.
      author, log_msg = metadata_db[cvs_item.metadata_id]
      cvs_generated_msg = 'file %s was initially added on branch %s.\n' % (
          self.cvs_file.basename,
          self[cvs_item.branch_ids[0]].symbol.name,)
      return log_msg == cvs_generated_msg
    else:
      return False

  def remove_unneeded_deletes(self, metadata_db):
    """Remove unneeded deletes for this file.

    If a file is added on a branch, then a trunk revision is added at
    the same time in the 'Dead' state.  This revision doesn't do
    anything useful, so delete it."""

    for id in self.root_ids:
      cvs_item = self[id]
      if self._delete_unneeded(cvs_item, metadata_db):
        Log().debug('Removing unnecessary delete %s' % (cvs_item,))

        # Delete cvs_item:
        self.root_ids.remove(cvs_item.id)
        del self[id]
        if cvs_item.next_id is not None:
          cvs_rev_next = self[cvs_item.next_id]
          cvs_rev_next.prev_id = None
          self.root_ids.add(cvs_rev_next.id)

        # Delete all CVSBranches rooted at this revision.  If there is
        # a CVSRevision on the branch, it should already be an add so
        # it doesn't have to be changed.
        for cvs_branch_id in cvs_item.branch_ids:
          cvs_branch = self[cvs_branch_id]
          del self[cvs_branch.id]

          if cvs_branch.next_id is not None:
            cvs_branch_next = self[cvs_branch.next_id]
            cvs_branch_next.first_on_branch_id = None
            cvs_branch_next.prev_id = None
            self.root_ids.add(cvs_branch_next.id)

        # Tagging a dead revision doesn't do anything, so remove any
        # tags that were set on 1.1:
        for cvs_tag_id in cvs_item.tag_ids:
          del self[cvs_tag_id]

        # This can only happen once per file, and we might have just
        # changed self.root_ids, so break out of the loop:
        break

  def _initial_branch_delete_unneeded(self, lod_items, metadata_db):
    """Return True iff the initial revision in LOD_ITEMS can be deleted."""

    if lod_items.cvs_branch is not None \
           and lod_items.cvs_branch.source_id is not None \
           and len(lod_items.cvs_revisions) >= 2:
      cvs_revision = lod_items.cvs_revisions[0]
      cvs_rev_source = self[lod_items.cvs_branch.source_id]
      if isinstance(cvs_revision, CVSRevisionAbsent) \
             and not cvs_revision.tag_ids \
             and not cvs_revision.branch_ids \
             and abs(cvs_revision.timestamp - cvs_rev_source.timestamp) <= 2:
        # FIXME: This message will not match if the RCS file was renamed
        # manually after it was created.
        author, log_msg = metadata_db[cvs_revision.metadata_id]
        return bool(re.match(
            r'file %s was added on branch .* on '
            r'\d{4}\-\d{2}\-\d{2} \d{2}\:\d{2}\:\d{2}( [\+\-]\d{4})?'
            '\n' % (re.escape(self.cvs_file.basename),),
            log_msg,
            ))
    return False

  def remove_initial_branch_deletes(self, metadata_db):
    """If the first revision on a branch is an unnecessary delete, remove it.

    If a file is added on a branch (whether or not it already existed
    on trunk), then new versions of CVS add a first branch revision in
    the 'dead' state (to indicate that the file did not exist on the
    branch when the branch was created) followed by the second branch
    revision, which is an add.  When we encounter this situation, we
    sever the branch from trunk and delete the first branch
    revision."""

    for lod_items in self.iter_lods():
      if self._initial_branch_delete_unneeded(lod_items, metadata_db):
        cvs_revision = lod_items.cvs_revisions[0]
        Log().debug(
            'Removing unnecessary initial branch delete %s' % (cvs_revision,)
            )
        cvs_branch = lod_items.cvs_branch
        cvs_rev_source = self[cvs_branch.source_id]
        cvs_rev_next = lod_items.cvs_revisions[1]

        # Delete cvs_revision:
        del self[cvs_revision.id]
        cvs_rev_next.prev_id = None
        self.root_ids.add(cvs_rev_next.id)
        cvs_rev_source.branch_commit_ids.remove(cvs_revision.id)

        # Delete the CVSBranch on which it is located:
        del self[cvs_branch.id]
        cvs_rev_source.branch_ids.remove(cvs_branch.id)

  def _exclude_tag(self, cvs_tag):
    """Exclude the specified CVS_TAG."""

    del self[cvs_tag.id]

    # A CVSTag is the successor of the CVSRevision that it
    # sprouts from.  Delete this tag from that revision's
    # tag_ids:
    self[cvs_tag.source_id].tag_ids.remove(cvs_tag.id)

  def _exclude_branch(self, lod_items):
    """Exclude the branch described by LOD_ITEMS, including its revisions.

    (Do not update the LOD_ITEMS instance itself.)

    If the LOD starts with non-trunk default branch revisions, leave
    them in place and do not delete the branch.  In this case, return
    True; otherwise return False"""

    if lod_items.cvs_revisions \
           and lod_items.cvs_revisions[0].default_branch_revision:
      for cvs_rev in lod_items.cvs_revisions:
        if not cvs_rev.default_branch_revision:
          # We've found the first non-NTDBR, and it's stored in cvs_rev:
          break
      else:
        # There was no revision following the NTDBRs:
        cvs_rev = None

      if cvs_rev:
        last_ntdbr = self[cvs_rev.prev_id]
        last_ntdbr.next_id = None
        while True:
          del self[cvs_rev.id]
          if cvs_rev.next_id is None:
            break
          cvs_rev = self[cvs_rev.next_id]

      return True

    else:
      if lod_items.cvs_branch is not None:
        # Delete the CVSBranch itself:
        cvs_branch = lod_items.cvs_branch

        del self[cvs_branch.id]

        # A CVSBranch is the successor of the CVSRevision that it
        # sprouts from.  Delete this branch from that revision's
        # branch_ids:
        self[cvs_branch.source_id].branch_ids.remove(cvs_branch.id)

      if lod_items.cvs_revisions:
        # The first CVSRevision on the branch has to be either detached
        # from the revision from which the branch sprang, or removed
        # from self.root_ids:
        cvs_rev = lod_items.cvs_revisions[0]
        if cvs_rev.prev_id is None:
          self.root_ids.remove(cvs_rev.id)
        else:
          self[cvs_rev.prev_id].branch_commit_ids.remove(cvs_rev.id)

        for cvs_rev in lod_items.cvs_revisions:
          del self[cvs_rev.id]
          # If cvs_rev is the last default revision on a non-trunk
          # default branch followed by a 1.2 revision, then the 1.2
          # revision depends on this one.  FIXME: It is questionable
          # whether this handling is correct, since the non-trunk
          # default branch revisions affect trunk and should therefore
          # not just be discarded even if --trunk-only.
          if cvs_rev.default_branch_next_id is not None:
            next = self[cvs_rev.default_branch_next_id]
            assert next.default_branch_prev_id == cvs_rev.id
            next.default_branch_prev_id = None
            if next.prev_id is None:
              self.root_ids.add(next.id)

      return False

  def graft_ntdbr_to_trunk(self):
    """Graft the non-trunk default branch revisions to trunk.

    They should already be alone on a CVSBranch-less branch."""

    ntdbr_lod_items = None
    for lod_items in self.iter_lods():
      if lod_items.cvs_revisions \
             and lod_items.cvs_revisions[0].default_branch_revision:
        assert lod_items.cvs_branch is None
        assert not lod_items.cvs_branches
        assert not lod_items.cvs_tags

        last_rev = lod_items.cvs_revisions[-1]

        if last_rev.default_branch_next_id is not None:
          rev_1_2 = self[last_rev.default_branch_next_id]
          rev_1_2.default_branch_prev_id = None
          rev_1_2.prev_id = last_rev.id
          self.root_ids.remove(rev_1_2.id)
          last_rev.default_branch_next_id = None
          last_rev.next_id = rev_1_2.id
          # The type of rev_1_2 was already adjusted in
          # adjust_ntdbrs(), so we don't have to change its type here.

        for cvs_rev in lod_items.cvs_revisions:
          cvs_rev.default_branch_revision = False
          cvs_rev.lod = self.trunk

        for cvs_branch in lod_items.cvs_branches:
          cvs_branch.source_lod = self.trunk

        for cvs_tag in lod_items.cvs_tags:
          cvs_tag.source_lod = self.trunk

        return

  def exclude_non_trunk(self):
    """Delete all tags and branches."""

    ntdbr_excluded = False
    for lod_items in self.iter_lods():
      for cvs_tag in lod_items.cvs_tags[:]:
        self._exclude_tag(cvs_tag)
        lod_items.cvs_tags.remove(cvs_tag)

      assert not lod_items.cvs_branches
      assert not lod_items.cvs_tags

      if not isinstance(lod_items.lod, Trunk):
        ntdbr_excluded |= self._exclude_branch(lod_items)

    if ntdbr_excluded:
      self.graft_ntdbr_to_trunk()

  def filter_excluded_symbols(self, revision_excluder):
    """Delete any excluded symbols and references to them.

    Call the revision_excluder's callback methods to let it know what
    is being excluded."""

    revision_excluder_started = False
    ntdbr_excluded = False
    for lod_items in self.iter_lods():
      # Delete any excluded tags:
      for cvs_tag in lod_items.cvs_tags[:]:
        if isinstance(cvs_tag.symbol, ExcludedSymbol):
          revision_excluder_started = True

          self._exclude_tag(cvs_tag)

          lod_items.cvs_tags.remove(cvs_tag)

      # Delete the whole branch if it is to be excluded:
      if isinstance(lod_items.lod, ExcludedSymbol):
        # A symbol can only be excluded if no other symbols spring
        # from it.  This was already checked in CollateSymbolsPass, so
        # these conditions should already be satisfied.
        assert not lod_items.cvs_branches
        assert not lod_items.cvs_tags

        revision_excluder_started = True

        ntdbr_excluded |= self._exclude_branch(lod_items)

    if ntdbr_excluded:
      self.graft_ntdbr_to_trunk()

    if revision_excluder_started:
      revision_excluder.process_file(self)
    else:
      revision_excluder.skip_file(self.cvs_file)

  def _mutate_branch_to_tag(self, cvs_branch):
    """Mutate the branch CVS_BRANCH into a tag."""

    if cvs_branch.next_id is not None:
      # This shouldn't happen because it was checked in
      # CollateSymbolsPass:
      raise FatalError('Attempt to exclude a branch with commits.')
    cvs_tag = CVSTag(
        cvs_branch.id, cvs_branch.cvs_file, cvs_branch.symbol,
        cvs_branch.source_lod, cvs_branch.source_id)
    self.add(cvs_tag)
    cvs_revision = self[cvs_tag.source_id]
    cvs_revision.branch_ids.remove(cvs_tag.id)
    cvs_revision.tag_ids.append(cvs_tag.id)

  def _mutate_tag_to_branch(self, cvs_tag):
    """Mutate the tag into a branch."""

    cvs_branch = CVSBranch(
        cvs_tag.id, cvs_tag.cvs_file, cvs_tag.symbol,
        None, cvs_tag.source_lod, cvs_tag.source_id, None)
    self.add(cvs_branch)
    cvs_revision = self[cvs_branch.source_id]
    cvs_revision.tag_ids.remove(cvs_branch.id)
    cvs_revision.branch_ids.append(cvs_branch.id)

  def _mutate_symbol(self, cvs_symbol):
    """Mutate CVS_SYMBOL if necessary."""

    symbol = cvs_symbol.symbol
    if isinstance(cvs_symbol, CVSBranch) and isinstance(symbol, Tag):
      self._mutate_branch_to_tag(cvs_symbol)
    elif isinstance(cvs_symbol, CVSTag) and isinstance(symbol, Branch):
      self._mutate_tag_to_branch(cvs_symbol)

  def mutate_symbols(self):
    """Force symbols to be tags/branches based on self.symbol_db."""

    for cvs_item in self.values():
      if isinstance(cvs_item, CVSRevision):
        # This CVSRevision may be affected by the mutation of any
        # CVSSymbols that it references, but there is nothing to do
        # here directly.
        pass
      elif isinstance(cvs_item, CVSSymbol):
        self._mutate_symbol(cvs_item)
      else:
        raise RuntimeError('Unknown cvs item type')

  def _adjust_tag_parent(self, cvs_tag):
    """Adjust the parent of CVS_TAG if possible and preferred.

    CVS_TAG is an instance of CVSTag.  This method must be called in
    leaf-to-trunk order."""

    # The Symbol that cvs_tag would like to have as a parent:
    preferred_parent = Ctx()._symbol_db.get_symbol(
        cvs_tag.symbol.preferred_parent_id)

    if cvs_tag.source_lod == preferred_parent:
      # The preferred parent is already the parent.
      return

    # The CVSRevision that is its direct parent:
    source = self[cvs_tag.source_id]
    assert isinstance(source, CVSRevision)

    if isinstance(preferred_parent, Trunk):
      # It is not possible to graft *onto* Trunk:
      return

    # Try to find the preferred parent among the possible parents:
    for branch_id in source.branch_ids:
      if self[branch_id].symbol == preferred_parent:
        # We found it!
        break
    else:
      # The preferred parent is not a possible parent in this file.
      return

    parent = self[branch_id]
    assert isinstance(parent, CVSBranch)

    Log().debug('Grafting %s from %s (on %s) onto %s' % (
                cvs_tag, source, source.lod, parent,))
    # Switch parent:
    source.tag_ids.remove(cvs_tag.id)
    parent.tag_ids.append(cvs_tag.id)
    cvs_tag.source_lod = parent.symbol
    cvs_tag.source_id = parent.id

  def _adjust_branch_parents(self, cvs_branch):
    """Adjust the parent of CVS_BRANCH if possible and preferred.

    CVS_BRANCH is an instance of CVSBranch.  This method must be
    called in leaf-to-trunk order."""

    # The Symbol that cvs_branch would like to have as a parent:
    preferred_parent = Ctx()._symbol_db.get_symbol(
        cvs_branch.symbol.preferred_parent_id)

    if cvs_branch.source_lod == preferred_parent:
      # The preferred parent is already the parent.
      return

    # The CVSRevision that is its direct parent:
    source = self[cvs_branch.source_id]
    # This is always a CVSRevision because we haven't adjusted it yet:
    assert isinstance(source, CVSRevision)

    if isinstance(preferred_parent, Trunk):
      # It is not possible to graft *onto* Trunk:
      return

    # Try to find the preferred parent among the possible parents:
    for branch_id in source.branch_ids:
      possible_parent = self[branch_id]
      if possible_parent.symbol == preferred_parent:
        # We found it!
        break
      elif possible_parent.symbol == cvs_branch.symbol:
        # Only branches that precede the branch to be adjusted are
        # considered possible parents.  Leave parentage unchanged:
        return
    else:
      # This point should never be reached.
      raise InternalError(
          'Possible parent search did not terminate as expected')

    parent = possible_parent
    assert isinstance(parent, CVSBranch)

    Log().debug('Grafting %s from %s (on %s) onto %s' % (
                cvs_branch, source, source.lod, parent,))
    # Switch parent:
    source.branch_ids.remove(cvs_branch.id)
    parent.branch_ids.append(cvs_branch.id)
    cvs_branch.source_lod = parent.symbol
    cvs_branch.source_id = parent.id

  def adjust_parents(self):
    """Adjust the parents of symbols to their preferred parents.

    If a CVSSymbol has a preferred parent that is different than its
    current parent, and if the preferred parent is an allowed parent
    of the CVSSymbol in this file, then graft the CVSSymbol onto its
    preferred parent."""

    for lod_items in self.iter_lods():
      for cvs_tag in lod_items.cvs_tags:
        self._adjust_tag_parent(cvs_tag)

      for cvs_branch in lod_items.cvs_branches:
        self._adjust_branch_parents(cvs_branch)

  def _get_revision_source(self, cvs_symbol):
    """Return the CVSRevision that is the ultimate source of CVS_SYMBOL."""

    while True:
      cvs_item = self[cvs_symbol.source_id]
      if isinstance(cvs_item, CVSRevision):
        return cvs_item
      else:
        cvs_symbol = cvs_item

  def refine_symbols(self):
    """Refine the types of the CVSSymbols in this file.

    Adjust the symbol types based on whether the source exists:
    CVSBranch vs. CVSBranchNoop and CVSTag vs. CVSTagNoop."""

    for lod_items in self.iter_lods():
      for cvs_tag in lod_items.cvs_tags:
        source = self._get_revision_source(cvs_tag)
        cvs_tag.__class__ = cvs_tag_type_map[
            isinstance(source, CVSRevisionModification)
            ]

      for cvs_branch in lod_items.cvs_branches:
        source = self._get_revision_source(cvs_branch)
        cvs_branch.__class__ = cvs_branch_type_map[
            isinstance(source, CVSRevisionModification)
            ]

  def record_opened_symbols(self):
    """Set CVSRevision.opened_symbols for the surviving revisions."""

    for cvs_item in self.values():
      if isinstance(cvs_item, (CVSRevision, CVSBranch)):
        cvs_item.opened_symbols = []
        for cvs_symbol_opened_id in cvs_item.get_cvs_symbol_ids_opened():
          cvs_symbol_opened = self[cvs_symbol_opened_id]
          cvs_item.opened_symbols.append(
              (cvs_symbol_opened.symbol.id, cvs_symbol_opened.id,)
              )

  def record_closed_symbols(self):
    """Set CVSRevision.closed_symbols for the surviving revisions.

    A CVSRevision closes the symbols that were opened by the CVSItems
    that the CVSRevision closes.  Got it?

    This method must be called after record_opened_symbols()."""

    for cvs_item in self.values():
      if isinstance(cvs_item, CVSRevision):
        cvs_item.closed_symbols = []
        for cvs_item_closed_id in cvs_item.get_ids_closed():
          cvs_item_closed = self[cvs_item_closed_id]
          cvs_item.closed_symbols.extend(cvs_item_closed.opened_symbols)

  def check_symbol_parent_lods(self):
    """Do a consistency check that CVSSymbol.source_lod is set correctly."""

    for cvs_item in self.values():
      if isinstance(cvs_item, CVSSymbol):
        source = self[cvs_item.source_id]
        if isinstance(source, CVSRevision):
          source_lod = source.lod
        else:
          source_lod = source.symbol

        if cvs_item.source_lod != source_lod:
          raise FatalError(
              'source_lod discrepancy for %r: %s != %s'
              % (cvs_item, cvs_item.source_lod, source_lod,)
              )


