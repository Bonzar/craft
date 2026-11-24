import unittest
from copy import deepcopy
from scope_pins import scope,checked_acl,stable
def site():
    return {'id':'test-project','current_user_role':'owner','access_mode':'custom',
       'current_live_url':'https://example.chatgpt.site/','mcp_connection':{
          'mcp_url':'https://example.chatgpt.site/mcp','plugin_id':'example'},
       'access_policy':{'project_id':'test-project','access_mode':'custom','external_visitor_count':0,
        'allowed_workspace_group_ids':[],'allowed_tenant_group_ids':[],'allowed_groups':[],'allowed_editors':[],
        'allowed_account_user_ids':['offline-owner'],'allowed_users':[{'account_user_id':'offline-owner','role':'owner'}]}}
class Pins(unittest.TestCase):
    def test_only_trimmed_success_shape_and_metadata_independence(self):
        s=site()
        p=scope(s,'test')
        self.assertEqual(len(p),3)
        s['access_policy']['revision']=8
        s['access_policy']['allowed_users'][0]['email']='discard@example.invalid'
        self.assertEqual(p,scope(s,'test'))
        self.assertTrue(stable(checked_acl(s)).endswith(b'\n'))
        self.assertNotIn('offline-owner',str(p))
    def test_foreign_editors_between_owner_and_native_origin_deny(self):
        for change in ({'allowed_editors':[{}]}, {'external_visitor_count':1}, {'allowed_groups':[{}]},
                       {'allowed_account_user_ids':['different']}, {'access_mode':'public'}):
            s=site();s['access_policy']|=change
            with self.subTest(change=change),self.assertRaises(ValueError):scope(s,'test')
        s=site();s['current_live_url']='https://foreign.chatgpt.site/'
        with self.assertRaises(ValueError):scope(s,'test')
