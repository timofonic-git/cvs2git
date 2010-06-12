# (Be in -*- python -*- mode.)
#
# ====================================================================
# Copyright (c) 2000-2008 CollabNet.  All rights reserved.
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

"""This module contains class SVNCommitItem."""


from cvs2svn_lib.context import Ctx


class SVNCommitItem:
  """A wrapper class for CVSRevision objects with no real purpose."""

  def __init__(self, cvs_rev):
    """Initialize instance."""

    self.cvs_rev = cvs_rev

  def has_keywords(self):
    return bool(self.cvs_rev.get_properties().get('svn:keywords', None))


