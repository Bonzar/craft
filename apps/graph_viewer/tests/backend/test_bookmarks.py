"""Bookmarks retain stable identities and report slice availability honestly."""
import unittest
import test_service as service


class BookmarkAvailability(unittest.TestCase):
    setUp=service.ViewerContract.setUp
    tearDown=service.ViewerContract.tearDown
    login=service.ViewerContract.login
    manifest=service.ViewerContract.manifest
    def test_hidden_and_missing_bookmarks_are_distinguished(self):
        self.login(); self.manifest()
        headers={'origin':'http://testserver','x-csrf-token':self.csrf}
        for id,node in [('hidden','old'),('missing','absent'),('visible','unicode')]:
            r=self.client.put('/api/v1/bookmarks/'+id,json={'provider_id':'assertion-memory','object_kind':'node','object_id':node},headers=headers)
            self.assertEqual(r.status_code,200,r.text)
        items={b['id']:b['availability'] for b in self.client.get('/api/v1/bookmarks').json()['items']}
        self.assertEqual(items,{'hidden':'hidden_in_slice','missing':'missing','visible':'available'})
