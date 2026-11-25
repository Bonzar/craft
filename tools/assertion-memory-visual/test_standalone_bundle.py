from unittest import TestCase, main
import runtime_companion as runtime
import standalone_bundle as bundle


class TestCombinedBundle(TestCase):
    def test_exact_same_original_build_for_frontend_and_backend(self):
        static=b'synthetic static TAR'
        ident=runtime.identity('assertion-memory-visual-v0.1.6','a'*40,'112','456',runtime.digest(static))
        companion=runtime.build(ident,{x:x.encode() for x in runtime.RUNTIME_FILES})
        raw=bundle.build(ident,static,companion)
        self.assertEqual(raw,bundle.build(ident,static,companion))
        self.assertEqual(bundle.verify(raw,ident,runtime.digest(raw)),(static,companion))
        with self.assertRaises(ValueError):
            bundle.verify(raw+b'x',ident,runtime.digest(raw))
        with self.assertRaises(ValueError):
            bundle.verify(raw,{**ident,'build_run_id':'457'},runtime.digest(raw))
        with self.assertRaises(ValueError):
            bundle.build(ident,b'other static',companion)


if __name__=='__main__':
    main()
