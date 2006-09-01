# (Be in -*- python -*- mode.)
#
# ====================================================================
# Copyright (c) 2000-2006 CollabNet.  All rights reserved.
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

"""This module contains the PairingsDatabase class."""


from cvs2svn_lib import config
from cvs2svn_lib.artifact_manager import artifact_manager
from cvs2svn_lib.database import DB_OPEN_READ
from cvs2svn_lib.database import DB_OPEN_NEW


class _NewPairingsDatabase:
  """A database to record symbol pairings.

  The pairings database records correspondences from branch symbols
  to all (branch and tag) symbols in the input RCS files.  Two
  symbols correspond when they open at the same revisions.  It is
  used to choose useful openings and closings to consider when
  finding sources for filling symbols.

  Records are name -> name, where the first name is a symbol and the
  second name is the branch best paired with that symbol."""

  def __init__(self):
    # A dictionary that maps symbol names to maps; each of those maps is
    # from branch names to scores.  self._tags['A']['B'] is the number
    # of RCS files in which 'A' and 'B' open simultaneously, or 'A'
    # opens from a revision on branch 'B'.
    self._tags = { }

  def register_branches(self, current_branch, branches, tags):
    """Register the openings of all of BRANCHES and TAGS from a
    revision on CURRENT_BRANCH."""

    for symbol_name in branches + tags:
      name_map = self._tags.setdefault(symbol_name, { })
      if current_branch is not None:
        name_map[current_branch] = name_map.get(current_branch, 0) + 1
      for branch in branches:
        if symbol_name != branch:
          name_map[branch] = name_map.get(branch, 0) + 1

  def write(self):
    f = open(artifact_manager.get_temp_file(config.PAIRINGS_LIST), "w")

    # Record the best entry for each symbolic name.
    for symbol_name, branches in self._tags.iteritems():
      branches = branches.items()
      if len(branches) == 0:
        continue
      branches.sort(lambda x, y: cmp (y[1], x[1]))
      f.write("%s %s\n" % (symbol_name, branches[0][0]))


class _OldPairingsDatabase:
  """Read-only access to pairings database.

  Records are name -> name, where the first name is a symbol and the
  second name is the branch best paired with that symbol.  The whole
  list is read into memory upon construction."""

  def __init__(self):
    # A dictionary that maps symbol names to the name of the best
    # candidate branch to open this symbol.
    self.tags = { }

    f = open(artifact_manager.get_temp_file(config.PAIRINGS_LIST))
    while 1:
      line = f.readline()
      if not line:
        break
      symbol_name, branch = line.split()
      self.tags[symbol_name] = branch


def PairingsDatabase(mode):
  """Open the PairingsDatabase in either NEW or READ mode.

  The class of the instance that is returned depends on MODE."""

  if mode == DB_OPEN_NEW:
    return _NewPairingsDatabase()
  elif mode == DB_OPEN_READ:
    return _OldPairingsDatabase()
  else:
    raise NotImplemented


