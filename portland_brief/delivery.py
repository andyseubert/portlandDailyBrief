"""Personal daily email with private SMTP settings and durable duplicate guards."""
import argparse
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, parseaddr
import hashlib
import json
import os
from pathlib import Path
import smtplib
import sqlite3
import ssl
import subprocess
from zoneinfo import ZoneInfo

from .config import load_config
from .pipeline import run_lock
from .repository_archive import validate
from .rss import summary_html
from .rendering import markdown
from .storage import Store
from .civic.store import CivicStore


def address(value):
    if not isinstance(value,str) or '\r' in value or '\n' in value:
        raise ValueError('A single valid email address is required')
    parsed=parseaddr(value)[1]
    if parsed != value or '@' not in parsed or ',' in parsed or ';' in parsed:
        raise ValueError('A single valid email address is required')
    return parsed


def message(doc, recipient, sender):
    recipient,sender=address(recipient),address(sender)
    msg=EmailMessage(policy=SMTP)
    msg['From']=sender;msg['To']=recipient
    msg['Subject']=doc['title']+' — '+doc['date']
    msg['Date']=format_datetime(datetime.fromisoformat(doc['generated_at']).astimezone(timezone.utc))
    key=hashlib.sha256((recipient+'\n'+doc['date']).encode()).hexdigest()[:32]
    msg['Message-ID']='<brief-'+key+'@'+sender.split('@')[1]+'>'
    msg.set_content(markdown(doc,repository=True))
    msg.add_alternative(summary_html(doc),subtype='html')
    return msg


def smtp_password(config):
    secret=os.getenv(config.get('password_env','PORTLAND_BRIEF_SMTP_PASSWORD'))
    if not secret and config.get('keychain_service'):
        result=subprocess.run(['/usr/bin/security','find-generic-password','-s',config['keychain_service'],'-w'],capture_output=True,text=True,timeout=10)
        if result.returncode == 0:
            secret=result.stdout.strip()
    if not secret or not config.get('smtp_host') or not config.get('smtp_username'):
        raise ValueError('Authenticated email sender is not configured; no email sent')
    mode=config.get('tls','starttls')
    if mode not in {'starttls','ssl'}:
        raise ValueError('SMTP must use authenticated TLS')
    return secret


def smtp_send(msg, config):
    secret=smtp_password(config)
    mode=config.get('tls','starttls')
    factory=smtplib.SMTP_SSL if mode=='ssl' else smtplib.SMTP
    kwargs={'timeout':30}
    if mode=='ssl':
        kwargs['context']=ssl.create_default_context()
    with factory(config['smtp_host'],config.get('smtp_port',465 if mode=='ssl' else 587),**kwargs) as server:
        if mode=='starttls':
            server.starttls(context=ssl.create_default_context())
        server.login(config['smtp_username'],secret)
        refused=server.send_message(msg)
        if refused:
            raise RuntimeError('Email recipient was refused')


def deliver(root, send=False, now=None, sender=smtp_send, scheduled=False):
    now=now or datetime.now(timezone.utc)
    settings,registry=load_config(root)
    tasks=json.loads((root/'config/tasks.json').read_text())
    policy=tasks.get('email_delivery',{})
    if scheduled and not policy.get('enabled'):
        return {'sent':False,'disabled':True}
    private=root/policy.get('local_config','data/email-delivery.json')
    if not private.exists():
        raise ValueError('Recipient and authenticated sender settings are missing; no email sent')
    config=json.loads(private.read_text())
    folder=root/settings['data_path']/'delivery';folder.mkdir(parents=True,exist_ok=True)
    with run_lock(folder/'email.lock'):
        store=Store(root/settings['data_path']/'editorial.sqlite3')
        civic=CivicStore(root/settings['data_path']/'civic.sqlite3')
        try:
            today=now.astimezone(ZoneInfo(settings['timezone'])).date().isoformat()
            if not store.db.execute('SELECT 1 FROM editions WHERE date=?',(today,)).fetchone():
                return {'sent':False,'waiting_for_edition':today}
            row,doc=store.get(today)
            if row['state'] not in {'needs_review','approved'}:
                raise ValueError('Held or rejected editions cannot be emailed')
            validate(doc,registry,civic)
            if doc.get('government_watch'):
                from .civic.government_watch import validate_snapshot
                validate_snapshot(civic,doc['government_watch'],now)
            msg=message(doc,config['to'],config['from'])
            preview=folder/(today+'.eml')
            preview.write_bytes(msg.as_bytes())
            if not send:
                return {'sent':False,'preview':str(preview)}
            if sender is smtp_send:
                smtp_password(config)  # Reject missing credentials before claiming transmission.
            with sqlite3.connect(folder/'email.sqlite3') as db:
                db.execute('CREATE TABLE IF NOT EXISTS deliveries(date TEXT,recipient_hash TEXT,state TEXT,at TEXT,message_id TEXT,PRIMARY KEY(date,recipient_hash))')
                recipient_hash=hashlib.sha256(config['to'].encode()).hexdigest()
                previous=db.execute('SELECT state FROM deliveries WHERE date=? AND recipient_hash=?',(today,recipient_hash)).fetchone()
                if previous:
                    if previous[0]=='sent':
                        return {'sent':False,'already_sent':today}
                    raise ValueError('Prior email delivery is uncertain; inspect the sender before retrying to avoid duplicates')
                # Commit before transmission. A crash/network ambiguity must not
                # cause an automatic second copy; reconcile the sender first.
                db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(today,recipient_hash,'sending',now.isoformat(),msg['Message-ID']))
                db.commit()
                try:
                    sender(msg,config)
                except Exception:
                    db.execute("UPDATE deliveries SET state='uncertain' WHERE date=? AND recipient_hash=?",(today,recipient_hash));db.commit()
                    raise
                db.execute("UPDATE deliveries SET state='sent' WHERE date=? AND recipient_hash=?",(today,recipient_hash));db.commit()
                return {'sent':True,'date':today,'message_id':msg['Message-ID']}
        finally:
            civic.close();store.close()


def main(argv=None):
    p=argparse.ArgumentParser(description='Preview or send the personal daily brief using private authenticated SMTP settings')
    p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--send',action='store_true')
    p.add_argument('--scheduled',action='store_true')
    args=p.parse_args(argv)
    try:
        print(json.dumps(deliver(args.root.resolve(),args.send,scheduled=args.scheduled),indent=2))
        return 0
    except (ValueError,RuntimeError,OSError,sqlite3.Error,smtplib.SMTPException,KeyError):
        # SMTP errors can contain credentials/addresses/server details; keep the
        # scheduler log generic, with private state retained for reconciliation.
        print('Email delivery not completed. Check private recipient/sender configuration and delivery state; no success is claimed.')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
