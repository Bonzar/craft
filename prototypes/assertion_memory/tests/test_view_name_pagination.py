"""A paged metadata payload pins both historical and current editorial names."""
import json
import unittest

from prototypes.assertion_memory.tests import test_view_names as fixtures
from prototypes.assertion_memory.service import MemoryService


class NamePaginationTest(unittest.TestCase):
    setUp=fixtures.ViewNamesTest.setUp
    prepare=fixtures.ViewNamesTest.prepare
    accepted=fixtures.ViewNamesTest.accepted
    append=fixtures.ViewNamesTest.append

    def setup_history(self):
        self.engine.model=None
        version=self.engine.build_view(self.spec.id)
        self.engine.model=self.model
        self.model.title='Valve maintenance conditions'
        self.append(self.accepted('first-presentation'))
        return MemoryService(self.store,self.engine),version

    def test_rename_between_metadata_pages_cannot_create_a_never_accepted_title(self):
        service,version=self.setup_history()
        whole=service.history(self.spec.id,version_id=version.id,section='metadata',limit=12000)
        cut=whole['content'].index(self.model.title)+len('Valve main')
        page=service.history(self.spec.id,version_id=version.id,section='metadata',limit=cut)
        first=page['content']
        self.model.title='Pressure inspection permits'
        self.append(self.accepted('second-presentation'))
        while page['continuation']:
            page=service.history(**page['continuation']['arguments'])
            first+=page['content']
        data=json.loads(first)
        self.assertEqual('Valve maintenance conditions',data['current_presentation']['display_title'])
        self.assertEqual('Shared scope overview',data['presentation']['display_title'])

    def test_metadata_generated_continuations_have_separate_presentation_pins(self):
        service,version=self.setup_history()
        page=service.history(self.spec.id,version_id=version.id,section='metadata',limit=100)
        args=page['continuation']['arguments']
        self.assertIn('presentation_cutoff',args)
        self.assertIn('name_watermark',args)
        self.assertIsNone(args['snapshot_id'])
        expected=service.history(self.spec.id,version_id=version.id,section='metadata',limit=12000,
            presentation_cutoff=args['presentation_cutoff'],name_watermark=args['name_watermark'])['content']
        combined=page['content']
        while page['continuation']:
            following=page['continuation']['arguments']
            self.assertEqual(args['presentation_cutoff'],following['presentation_cutoff'])
            self.assertEqual(args['name_watermark'],following['name_watermark'])
            page=service.history(**following);combined+=page['content']
        self.assertEqual(expected,combined)

    def test_unpinned_noninitial_metadata_offset_is_rejected(self):
        service,version=self.setup_history()
        with self.assertRaisesRegex(ValueError,'presentation'):
            service.history(self.spec.id,version_id=version.id,section='metadata',offset=10,limit=100)


if __name__=='__main__':unittest.main()
