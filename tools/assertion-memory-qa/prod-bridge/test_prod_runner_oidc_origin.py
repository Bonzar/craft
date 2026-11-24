from unittest import TestCase,main
from unittest.mock import patch
from urllib.error import URLError
import prod_runner as runner

class IssuanceTests(TestCase):
    raw='https://vstoken-1.actions.githubusercontent.com/_services/token?a=%2f+%20&blank='
    aud='urn:assertion-memory:synthetic'
    secret='diagnostic-never-log'

    def env(self,url=None):
        return {'ACTIONS_ID_TOKEN_REQUEST_URL':self.raw if url is None else url,
                'ACTIONS_ID_TOKEN_REQUEST_TOKEN':self.secret}

    def test_trusted_exact_dns_and_query_bytes(self):
        for host in ('pipelines','region-dotted-1','a'):
            raw=self.raw.replace('vstoken-1',host)
            self.assertEqual(runner.issuance_url(raw,self.aud),
                raw+'&audience=urn%3Aassertion-memory%3Asynthetic')

    def test_every_other_origin_fails_before_bearer_send(self):
        for item in (
            self.raw.replace('https:','http:'),
            self.raw.replace('https://','https://u@'),
            self.raw.replace('.com/', '.com:443/'),
            self.raw.replace('.com/','.com.evil.test/'),
            self.raw.replace('.com/','.com./'),
            self.raw.replace('vstoken-1','two.levels'),
            self.raw.replace('vstoken-1','-bad'),
            self.raw.replace('vstoken-1','bad_'),
            self.raw.replace('vstoken-1.',''),
            self.raw.replace('actions.githubusercontent.com','githubusercontent.com'),
            self.raw+'#fragment',self.raw+'#',self.raw+'\\evil',
            self.raw+'&%61udience=preexisting',self.raw+'&audience=',
            self.raw.replace('token?', 'to\nken?'),self.raw.split('?')[0],
        ):
            with self.subTest(item=item),patch.object(runner,'build_opener') as opener:
                with self.assertRaisesRegex(runner.ProtectedStageError,'^github-oidc-issuance-origin$'):
                    runner.mint(self.aud,env=self.env(item))
                opener.assert_not_called()

    def test_request_shape_and_response_no_sensitive_log(self):
        url=runner.issuance_url(self.raw,self.aud)
        class Reply:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def geturl(self): return url
            def read(self,n): return b'{"value":"ab.cd.ef"}'
        with patch.object(runner,'build_opener') as opener:
            opener.return_value.open.return_value=Reply()
            self.assertEqual(runner.mint(self.aud,env=self.env()),'ab.cd.ef')
            req=opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url,url)
            self.assertEqual(req.headers['Authorization'],'Bearer '+self.secret)
        with patch.object(runner,'build_opener') as opener:
            opener.return_value.open.side_effect=URLError(self.secret)
            with self.assertRaisesRegex(runner.ProtectedStageError,'^github-oidc-issuance-http$'):
                runner.mint(self.aud,env=self.env())
        class Wrong(Reply):
            def geturl(self): return 'https://different.invalid/'
        with patch.object(runner,'build_opener') as opener:
            opener.return_value.open.return_value=Wrong()
            with self.assertRaisesRegex(runner.ProtectedStageError,'^github-oidc-issuance-http$'):
                runner.mint(self.aud,env=self.env())
        class Malformed(Reply):
            def read(self,n): return b'{"value":"secret-not-jwt"}'
        with patch.object(runner,'build_opener') as opener:
            opener.return_value.open.return_value=Malformed()
            with self.assertRaisesRegex(runner.ProtectedStageError,'^github-oidc-issuance-response$'):
                runner.mint(self.aud,env=self.env())

if __name__=='__main__': main()
