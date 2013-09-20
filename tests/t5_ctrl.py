'''
t5_ctrl.py - this file is part of S3QL (http://s3ql.googlecode.com)

Copyright (C) 2008-2009 Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import

import s3ql.ctrl
import sys
import t4_fuse
import unittest2 as unittest

class CtrlTests(t4_fuse.fuse_tests):

    def runTest(self):
        self.mkfs()
        self.mount()
        self.tst_ctrl_flush()
        self.umount()
        self.fsck()

    def tst_ctrl_flush(self):

        try:
            s3ql.ctrl.main(['flushcache', self.mnt_dir])
        except:
            sys.excepthook(*sys.exc_info())
            self.fail("s3qlctrl raised exception")


# Somehow important according to pyunit documentation
def suite():
    return unittest.makeSuite(CtrlTests)


# Allow calling from command line
if __name__ == "__main__":
    unittest.main()
