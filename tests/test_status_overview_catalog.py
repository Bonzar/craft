"""Cheap overview diagnostics leave navigation scans behind explicit continuations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import derived_directory
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.capture import CaptureInput


class StatusOverviewCatalogTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = MemoryStore(Path(self.directory.name)/'memory.sqlite3')
        self.addCleanup(self.store.close)
        self.service = MemoryService(self.store)
        self.service.capture_message('fixture-session','message','user','user',
            'Synthetic delivery needs a review.',0,'fixture://message',scope='fixture')

    def test_overview_retains_counts_and_catalog_cursor_without_catalog_scan(self):
        with patch('prototypes.assertion_memory.navigation_catalog.descriptor',
                   side_effect=AssertionError('overview must not build navigation catalog')):
            result = self.service.status(limit=1)
        self.assertEqual(result['event_count'],1)
        self.assertEqual(result['pending_count'],1)
        entry = result['derived_overviews']['entries'][0]
        self.assertEqual(entry['scope'],'fixture')
        self.assertEqual(entry['source_pending_count'],1)
        self.assertFalse(entry['catalog']['details_inline'])
        self.assertEqual(entry['catalog']['continuation'],{'tool':'memory_status',
            'arguments':{'scope':'fixture','section':'navigation_catalog','offset':0,'limit':100}})
        self.assertIn('pagination',result)

    def test_explicit_derived_directory_keeps_catalog_default(self):
        catalog = {'catalog_id':'fixture-catalog','total':0,'continuation':None}
        with patch('prototypes.assertion_memory.navigation_catalog.descriptor',return_value=catalog) as scan:
            result = derived_directory.directory(self.service,'fixture',0,10,lambda value:True)
        scan.assert_called_once_with(self.service,'fixture')
        self.assertEqual(result['entries'][0]['catalog'],catalog)

    def test_queue_breakdown_accounts_for_raw_and_assertion_jobs_without_scope_leak(self):
        for scope in ('fixture','other'):
            self.service.queue.capture(CaptureInput(text='A synthetic requirement.',actor='user',
                source='fixture://requirement',episode='fixture',scope=scope))
        result=self.service.status(scope='fixture',limit=1)
        self.assertEqual(result['job_counts_by_phase'],
            {'extract':{'waiting_model':1},'link':{'pending':1}})
        self.assertEqual(result['job_counts_by_origin'],
            {'raw_message':{'waiting_model':1},'assertion':{'pending':1}})
        self.assertEqual(sum(sum(value.values()) for value in result['job_counts_by_phase'].values()),2)
