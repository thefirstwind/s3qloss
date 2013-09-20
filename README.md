s3qloss
=======

s3ql plugin for aliyun oss

=======

------------------------
Installation:Test
------------------------

>># git clone https://github.com/thefirstwind/s3qloss
>># cd s3qloss


for contribute 
    # vi ~/.s3ql/authinfo
    # chmod 600 ~/.s3ql/authinfo
    * see contrib/authinfo
#>>>>>>>>>
[gs]
storage-url: gs://ossfs
backend-login: GOOGGURDYPMCGGOLVY53
backend-password: VxJASoLVq3N+wJGC6OGWmRdOxg+1EZ4hvUIDCsZU
[oss]
storage-url: oss://ossbucket
backend-login: IJ1a5Kg1ujqsrOfb
backend-password: EKQWar0RJAqrZdeM3jXfeHeWB5LeNf
#>>>>>>>>>


for test
    # vi ~/.s3ql/authinfo2
    # chmod 600 ~/.s3ql/authinfo2
    * see contrib/authinfo2
  
#>>>>>>>>>
[gs-test]
backend-login: GOOGGURDYPMCGGOLVY53
backend-password: VxJASoLVq3N+wJGC6OGWmRdOxg+1EZ4hvUIDCsZU
test-fs: gs://ossfstest
[oss-test]
backend-login: IJ1a5Kg1ujqsrOfb
backend-password: EKQWar0RJAqrZdeM3jXfeHeWB5LeNf
test-fs: oss://ossbucket-test
#>>>>>>>>>


>># python setup.py test
