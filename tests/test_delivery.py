from copy import deepcopy
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from portland_brief.delivery import deliver, message, smtp_send
from portland_brief.repository_archive import export
from portland_brief.rss import render
from portland_brief.storage import Store
from tests.test_brief import doc, ROOT


class RSSDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        shutil.copytree(ROOT/'config',self.root/'config')
        self.store=Store(self.root/'data/editorial.sqlite3')
        self.record=doc()
        self.store.save_run('fixture',self.record);self.store.save_edition('fixture',self.record)
        self.config={'to':'reader@example.test','from':'brief@example.test','smtp_host':'smtp.example.test','smtp_username':'fixture'}
        (self.root/'data/email-delivery.json').write_text(json.dumps(self.config))
        self.now=datetime(2026,10,10,3,tzinfo=timezone.utc)

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def test_rss_is_parseable_dated_deterministic_and_summary_only(self):
        export(self.root)
        raw=(self.root/'feed.xml').read_text();feed=ET.fromstring(raw)
        item=feed.find('channel/item')
        self.assertEqual(item.findtext('title'),'Portland Daily Brief — 2026-10-09')
        self.assertEqual(item.find('guid').get('isPermaLink'),'false')
        self.assertNotIn(self.record['evidence']['e_test']['text'],item.findtext('description'))
        self.assertIn(self.record['stories'][0]['sentences'][0],item.findtext('description'))
        self.assertNotIn('data/runs/',raw)
        export(self.root);self.assertEqual((self.root/'feed.xml').read_text(),raw)

    def test_feed_preserves_valid_output_when_new_edition_has_bad_citation(self):
        export(self.root);before=(self.root/'feed.xml').read_bytes()
        bad=deepcopy(self.record);bad['stories'][0]['citation_ids']=['invented']
        self.store.save_run('bad',bad);self.store.save_edition('bad',bad)
        with self.assertRaises(KeyError):
            export(self.root)
        self.assertEqual((self.root/'feed.xml').read_bytes(),before)

    def test_rss_correction_keeps_same_dated_item_identity(self):
        config=json.loads((self.root/'config/tasks.json').read_text())['rss']
        before=ET.fromstring(render([self.record],config)).findtext('channel/item/guid')
        corrected=deepcopy(self.record);corrected['generated_at']='2026-10-10T03:00:00+00:00'
        after=ET.fromstring(render([corrected],config)).findtext('channel/item/guid')
        self.assertEqual(before,after)

    def test_email_preview_has_both_formats_without_transmitting(self):
        sent=[]
        result=deliver(self.root,now=self.now,sender=lambda *args:sent.append(args))
        self.assertFalse(result['sent']);self.assertEqual(sent,[])
        msg=BytesParser(policy=policy.default).parsebytes(Path(result['preview']).read_bytes())
        self.assertEqual(msg['To'],'reader@example.test')
        self.assertIsNotNone(msg.get_body(preferencelist=('html',)))
        self.assertIsNotNone(msg.get_body(preferencelist=('plain',)))
        self.assertNotIn(self.record['evidence']['e_test']['text'],msg.get_body(preferencelist=('html',)).get_content())

    def test_successful_send_is_deduplicated_across_retries_and_corrections(self):
        sent=[]
        sender=lambda *args:sent.append(args)
        self.assertTrue(deliver(self.root,True,self.now,sender)['sent'])
        self.assertTrue(deliver(self.root,True,self.now,sender)['already_sent'])
        corrected=deepcopy(self.record);corrected['stories'][0]['headline']='Corrected fixture headline'
        self.store.save_run('correction',corrected);self.store.save_edition('correction',corrected)
        self.assertTrue(deliver(self.root,True,self.now,sender)['already_sent'])
        self.assertEqual(len(sent),1)

    def test_ambiguous_transport_failure_is_not_automatically_resent(self):
        def broken(*args):
            raise OSError('fixture failure after possible acceptance')
        with self.assertRaises(OSError):
            deliver(self.root,True,self.now,broken)
        calls=[]
        with self.assertRaisesRegex(ValueError,'uncertain'):
            deliver(self.root,True,self.now,lambda *args:calls.append(args))
        self.assertEqual(calls,[])

    def test_missing_today_waits_instead_of_sending_yesterdays_brief(self):
        calls=[]
        result=deliver(self.root,True,datetime(2026,10,10,23,tzinfo=timezone.utc),lambda *args:calls.append(args))
        self.assertEqual(result['waiting_for_edition'],'2026-10-10');self.assertEqual(calls,[])

    def test_scheduled_delivery_stays_off_until_configured_and_enabled(self):
        result=deliver(self.root,True,self.now,scheduled=True)
        self.assertTrue(result['disabled'])

    def test_missing_credentials_do_not_claim_an_uncertain_delivery(self):
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(ValueError,'not configured'):
                deliver(self.root,True,self.now)
        self.assertFalse((self.root/'data/delivery/email.sqlite3').exists())

    def test_header_injection_or_multiple_recipients_are_rejected(self):
        for address in ['x@example.test\nBcc: y@example.test','x@example.test,y@example.test']:
            with self.assertRaises(ValueError):
                message(self.record,address,'brief@example.test')

    def test_smtp_transport_uses_tls_login_and_single_send(self):
        msg=message(self.record,self.config['to'],self.config['from'])
        with patch.dict('os.environ',{'PORTLAND_BRIEF_SMTP_PASSWORD':'fixture-secret'}),patch('portland_brief.delivery.smtplib.SMTP') as client:
            client.return_value.__enter__.return_value.send_message.return_value={}
            smtp_send(msg,self.config)
            server=client.return_value.__enter__.return_value
            server.starttls.assert_called_once();server.login.assert_called_once_with('fixture','fixture-secret')
            server.send_message.assert_called_once_with(msg)
