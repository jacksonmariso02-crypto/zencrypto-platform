"""ZenCrypto API reference implementation. PAPER MONEY ONLY. NOT A LIVE EXCHANGE."""
import os, sqlite3, secrets, hashlib, hmac, time, json
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

DB=os.getenv('DATABASE_PATH','zencrypto.db')
SECRET=os.getenv('APP_SECRET') or secrets.token_urlsafe(48)
TOKEN=URLSafeTimedSerializer(SECRET, salt='zencrypto-session-v1')
app=FastAPI(title='ZenCrypto API — sandbox only',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('CORS_ORIGINS','http://localhost:5173').split(','),allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])
@contextmanager
def db():
 c=sqlite3.connect(DB,timeout=15,isolation_level=None); c.row_factory=sqlite3.Row
 c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=15000')
 try: yield c
 finally: c.close()
@app.on_event('startup')
def init():
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',kyc_status TEXT NOT NULL DEFAULT 'not_submitted',created_at INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(user_id) REFERENCES users(id));
  CREATE TABLE IF NOT EXISTS kyc(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,legal_name TEXT NOT NULL,country TEXT NOT NULL,provider_reference TEXT,submitted_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
  CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,currency TEXT NOT NULL,UNIQUE(user_id,currency),FOREIGN KEY(user_id) REFERENCES users(id));
  CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY,kind TEXT NOT NULL,reference TEXT UNIQUE NOT NULL,created_at INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS postings(id INTEGER PRIMARY KEY,journal_id TEXT NOT NULL,account_id INTEGER NOT NULL,amount_minor INTEGER NOT NULL,FOREIGN KEY(journal_id) REFERENCES journal(id),FOREIGN KEY(account_id) REFERENCES accounts(id));
  CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,currency TEXT NOT NULL,amount_minor INTEGER NOT NULL,destination TEXT,status TEXT NOT NULL,created_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
  CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor_id INTEGER,event TEXT NOT NULL,detail TEXT NOT NULL,created_at INTEGER NOT NULL);
  CREATE INDEX IF NOT EXISTS postings_account_idx ON postings(account_id);
  CREATE TABLE IF NOT EXISTS jurisdiction_policy(country TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'blocked', legal_reference TEXT, reviewed_at INTEGER);
  CREATE TABLE IF NOT EXISTS compliance_cases(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at INTEGER NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
  CREATE TABLE IF NOT EXISTS sandbox_operations(idempotency_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,operation TEXT NOT NULL,response TEXT NOT NULL,created_at INTEGER NOT NULL);
  CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journal BEGIN SELECT RAISE(ABORT,'immutable journal'); END;
  CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journal BEGIN SELECT RAISE(ABORT,'immutable journal'); END;
  CREATE TRIGGER IF NOT EXISTS postings_no_update BEFORE UPDATE ON postings BEGIN SELECT RAISE(ABORT,'immutable postings'); END;
  CREATE TRIGGER IF NOT EXISTS postings_no_delete BEFORE DELETE ON postings BEGIN SELECT RAISE(ABORT,'immutable postings'); END;''')
def audit(c,uid,event,detail): c.execute('INSERT INTO audit(actor_id,event,detail,created_at) VALUES(?,?,?,?)',(uid,event,json.dumps(detail),int(time.time())))
def hash_pw(p):
 salt=secrets.token_bytes(16); digest=hashlib.pbkdf2_hmac('sha256',p.encode(),salt,600000)
 return f'{salt.hex()}:{digest.hex()}'
def verify_pw(p,s):
 try:
  salt,digest=s.split(':'); return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(salt),600000),bytes.fromhex(digest))
 except Exception: return False
def amount_minor(value):
 try:
  d=Decimal(str(value)); scaled=d*100
  if not d.is_finite() or d<=0 or scaled!=scaled.to_integral_value() or scaled>100000000000: raise ValueError()
  return int(scaled)
 except (InvalidOperation,ValueError): raise HTTPException(422,'Invalid amount: positive value with at most 2 decimal places required')
def require_user(authorization: str|None=Header(default=None)):
 if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Authentication required')
 try: sid=TOKEN.loads(authorization[7:],max_age=3600)
 except (BadSignature,SignatureExpired): raise HTTPException(401,'Invalid or expired session')
 with db() as c:
  u=c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.id=? AND s.revoked=0 AND s.expires_at>?',(sid,int(time.time()))).fetchone()
  if not u: raise HTTPException(401,'Session expired')
  return dict(u)|{'session_id':sid}
class Register(BaseModel):
 email:str=Field(min_length=5,max_length=254)
 password:str=Field(min_length=12,max_length=128)
class Login(BaseModel):
 email:str
 password:str
class Kyc(BaseModel):
 legal_name:str=Field(min_length=2,max_length=150)
 country:str=Field(min_length=2,max_length=80)
class Money(BaseModel):
 currency:str='USD'
 amount:str
class Withdrawal(Money):
 destination:str=Field(min_length=3,max_length=200)
@app.get('/health')
def health(): return {'status':'ok','mode':'SANDBOX_ONLY','live_funds_enabled':False}
@app.post('/auth/register',status_code=201)
def register(body:Register):
 email=body.email.strip().lower()
 if '@' not in email: raise HTTPException(422,'Invalid email')
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE'); cur=c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',(email,hash_pw(body.password),int(time.time())))
   c.execute('INSERT INTO accounts(user_id,currency) VALUES(?,?)',(cur.lastrowid,'USD')); audit(c,cur.lastrowid,'register',{}); c.execute('COMMIT')
  except sqlite3.IntegrityError: c.execute('ROLLBACK'); raise HTTPException(409,'Email already registered')
 return {'message':'Account created. KYC required before any future real-money access.'}
@app.post('/auth/login')
def login(body:Login):
 with db() as c:
  u=c.execute('SELECT * FROM users WHERE email=?',(body.email.strip().lower(),)).fetchone()
  if not u or not verify_pw(body.password,u['password_hash']): raise HTTPException(401,'Invalid credentials')
  sid=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions(id,user_id,expires_at) VALUES(?,?,?)',(sid,u['id'],int(time.time())+3600)); audit(c,u['id'],'login',{})
 return {'access_token':TOKEN.dumps(sid),'token_type':'bearer','expires_in':3600}
@app.post('/auth/logout')
def logout(u=Depends(require_user)):
 with db() as c: c.execute('UPDATE sessions SET revoked=1 WHERE id=?',(u['session_id'],)); audit(c,u['id'],'logout',{})
 return {'message':'Logged out'}
@app.get('/account/me')
def me(u=Depends(require_user)): return {'id':u['id'],'email':u['email'],'kyc_status':u['kyc_status'],'role':u['role'],'mode':'SANDBOX_ONLY'}
@app.post('/kyc/submit')
def submit_kyc(body:Kyc,u=Depends(require_user)):
 with db() as c:
  c.execute('INSERT INTO kyc(user_id,legal_name,country,submitted_at) VALUES(?,?,?,?)',(u['id'],body.legal_name,body.country,int(time.time())))
  c.execute("UPDATE users SET kyc_status='pending_provider_verification' WHERE id=?",(u['id'],)); audit(c,u['id'],'kyc_submitted',{'country':body.country})
 return {'status':'pending_provider_verification','notice':'No identity verification is performed by this demo. Integrate a certified KYC vendor.'}
@app.get('/account/balances')
def balances(u=Depends(require_user)):
 with db() as c:
  rows=c.execute('SELECT a.currency,COALESCE(SUM(p.amount_minor),0) balance FROM accounts a LEFT JOIN postings p ON p.account_id=a.id WHERE a.user_id=? GROUP BY a.id',(u['id'],)).fetchall()
 return {'balances':[{'currency':r['currency'],'amount':f"{Decimal(r['balance'])/100:.2f}"} for r in rows],'mode':'SANDBOX_ONLY'}
@app.post('/deposits/request',status_code=201)
def deposit(body:Money,u=Depends(require_user)):
 if body.currency!='USD': raise HTTPException(422,'Sandbox only supports USD')
 amt=amount_minor(body.amount); rid=secrets.token_urlsafe(18)
 with db() as c:
  c.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',(rid,u['id'],'deposit',body.currency,amt,None,'awaiting_external_provider',int(time.time())))
  audit(c,u['id'],'deposit_requested',{'request_id':rid})
 return {'request_id':rid,'status':'awaiting_external_provider','deposit_address':None,'notice':'No payment or blockchain transfer is accepted. No balance credited.'}
@app.post('/withdrawals/request',status_code=201)
def withdrawal(body:Withdrawal,u=Depends(require_user)):
 if body.currency!='USD': raise HTTPException(422,'Sandbox only supports USD')
 amt=amount_minor(body.amount); rid=secrets.token_urlsafe(18)
 with db() as c:
  c.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',(rid,u['id'],'withdrawal',body.currency,amt,body.destination,'blocked_live_disabled',int(time.time())))
  audit(c,u['id'],'withdrawal_blocked',{'request_id':rid})
 return {'request_id':rid,'status':'blocked_live_disabled','notice':'No funds moved. Provider, AML, sanctions, approvals and ledger reconciliation required.'}
@app.get('/account/requests')
def requests(u=Depends(require_user)):
 with db() as c: rows=c.execute('SELECT id,kind,currency,amount_minor,status,created_at FROM requests WHERE user_id=? ORDER BY created_at DESC LIMIT 100',(u['id'],)).fetchall()
 return {'requests':[dict(r)|{'amount':f"{Decimal(r['amount_minor'])/100:.2f}"} for r in rows]}
@app.post('/trade/orders')
def orders(u=Depends(require_user)):
 raise HTTPException(503,'Live order execution is disabled. Integrate an authorized execution provider and settlement reconciliation.')
@app.get('/admin/audit')
def admin_audit(u=Depends(require_user)):
 if u['role']!='admin': raise HTTPException(403,'Admin role required')
 with db() as c: rows=c.execute('SELECT id,actor_id,event,detail,created_at FROM audit ORDER BY id DESC LIMIT 100').fetchall()
 return {'events':[dict(r) for r in rows]}

# Global compliance and paper-only balanced ledger extensions.
# These endpoints deliberately cannot move real assets or mark KYC as verified.
from typing import Literal

class PaperTransfer(BaseModel):
    amount: str
    currency: Literal['USD'] = 'USD'

class CaseCreate(BaseModel):
    reason: str = Field(min_length=8, max_length=500)

@app.get('/compliance/jurisdictions/{country}')
def jurisdiction(country: str):
    code = country.upper()
    if len(code) != 2 or not code.isalpha():
        raise HTTPException(422, 'Use a two-letter ISO country code')
    with db() as c:
        r = c.execute('SELECT status,legal_reference FROM jurisdiction_policy WHERE country=?',(code,)).fetchone()
    return {'country':code,'live_trading_allowed':False,'policy_status':r['status'] if r else 'blocked',
            'notice':'Live funds are globally disabled. A policy entry is not a licence.'}

@app.get('/compliance/status')
def compliance_status(u=Depends(require_user)):
    with db() as c:
        r=c.execute('SELECT country FROM kyc WHERE user_id=? ORDER BY id DESC LIMIT 1',(u['id'],)).fetchone()
        count=c.execute("SELECT COUNT(*) FROM compliance_cases WHERE user_id=? AND status='open'",(u['id'],)).fetchone()[0]
    return {'kyc_status':u['kyc_status'],'declared_country':r['country'] if r else None,
            'open_cases':count,'real_money_allowed':False,'withdrawals_allowed':False}

@app.post('/compliance/cases',status_code=201)
def open_case(body:CaseCreate,u=Depends(require_user)):
    rid=secrets.token_urlsafe(18)
    with db() as c:
        c.execute('INSERT INTO compliance_cases(id,user_id,reason,created_at) VALUES(?,?,?,?)',
                  (rid,u['id'],body.reason,int(time.time())))
        audit(c,u['id'],'compliance_case_opened',{'case_id':rid})
    return {'case_id':rid,'status':'open','notice':'Not a substitute for AML monitoring or regulatory reporting.'}

@app.get('/admin/compliance/cases')
def admin_cases(u=Depends(require_user)):
    if u['role']!='admin': raise HTTPException(403,'Admin role required')
    with db() as c:
        rows=c.execute('SELECT id,user_id,reason,status,created_at FROM compliance_cases ORDER BY created_at DESC LIMIT 100').fetchall()
    return {'cases':[dict(x) for x in rows]}

def post_balanced(c, kind, reference, entries):
    # entries: list[(account_id, signed integer cents)]; single currency only.
    if len(entries)<2 or sum(value for _,value in entries)!=0 or any(value==0 for _,value in entries):
        raise HTTPException(422,'Unbalanced journal')
    jid=secrets.token_urlsafe(18)
    c.execute('INSERT INTO journal(id,kind,reference,created_at) VALUES(?,?,?,?)',
              (jid,kind,reference,int(time.time())))
    for aid,value in entries:
        c.execute('INSERT INTO postings(journal_id,account_id,amount_minor) VALUES(?,?,?)',(jid,aid,value))
    return jid

@app.post('/sandbox/faucet',status_code=201)
def sandbox_faucet(body:PaperTransfer, idempotency_key: str=Header(min_length=8,max_length=100),u=Depends(require_user)):
    # Paper credits are visibly labeled, limited to $1,000 total per user.
    # Never use this route for real money or production customer balances.
    amt=amount_minor(body.amount)
    if amt>100000: raise HTTPException(422,'Sandbox maximum is 1000 USD per request')
    with db() as c:
        try:
            c.execute('BEGIN IMMEDIATE')
            prev=c.execute('SELECT * FROM sandbox_operations WHERE idempotency_key=?',(idempotency_key,)).fetchone()
            if prev:
                if prev['user_id']!=u['id'] or prev['operation']!=f'faucet:{amt}:USD':
                    raise HTTPException(409,'Idempotency key reused for different operation')
                c.execute('COMMIT')
                return json.loads(prev['response'])
            issued=c.execute("SELECT COALESCE(SUM(CAST(json_extract(response,'$.credited_minor') AS INTEGER)),0) FROM sandbox_operations WHERE user_id=? AND operation LIKE 'faucet:%'",(u['id'],)).fetchone()[0]
            if issued+amt>100000: raise HTTPException(422,'Lifetime sandbox faucet limit is 1000 USD')
            user_account=c.execute("SELECT id FROM accounts WHERE user_id=? AND currency='USD'",(u['id'],)).fetchone()['id']
            # House account is a separately named internal account (user_id NULL prohibited by schema),
            # so use a dedicated reserved system user with an unguessable password.
            sysuser=c.execute("SELECT id FROM users WHERE email='sandbox-house@internal.invalid'").fetchone()
            if not sysuser:
                sysid=c.execute('INSERT INTO users(email,password_hash,role,created_at) VALUES(?,?,?,?)',
                                ('sandbox-house@internal.invalid',hash_pw(secrets.token_urlsafe(64)),'system',int(time.time()))).lastrowid
                house=c.execute("INSERT INTO accounts(user_id,currency) VALUES(?,'USD')",(sysid,)).lastrowid
            else:
                house=c.execute("SELECT id FROM accounts WHERE user_id=? AND currency='USD'",(sysuser['id'],)).fetchone()['id']
            jid=post_balanced(c,'sandbox_faucet','faucet:'+idempotency_key,[(house,-amt),(user_account,amt)])
            response={'mode':'PAPER_ONLY','credited_minor':amt,'currency':'USD','journal_id':jid}
            c.execute('INSERT INTO sandbox_operations VALUES(?,?,?,?,?)',
                      (idempotency_key,u['id'],f'faucet:{amt}:USD',json.dumps(response),int(time.time())))
            audit(c,u['id'],'sandbox_credit',{'journal_id':jid,'amount_minor':amt})
            c.execute('COMMIT')
            return response
        except Exception:
            if c.in_transaction: c.execute('ROLLBACK')
            raise

@app.get('/account/ledger')
def account_ledger(u=Depends(require_user)):
    with db() as c:
        rows=c.execute("""SELECT j.id,j.kind,j.reference,j.created_at,a.currency,p.amount_minor
           FROM postings p JOIN accounts a ON a.id=p.account_id JOIN journal j ON j.id=p.journal_id
           WHERE a.user_id=? ORDER BY j.created_at DESC LIMIT 100""",(u['id'],)).fetchall()
    return {'mode':'PAPER_ONLY','entries':[dict(r) for r in rows]}

@app.get('/admin/ledger/integrity')
def ledger_integrity(u=Depends(require_user)):
    if u['role']!='admin': raise HTTPException(403,'Admin role required')
    with db() as c:
        bad=c.execute("""SELECT j.id FROM journal j LEFT JOIN postings p ON p.journal_id=j.id
           GROUP BY j.id HAVING COUNT(p.id)<2 OR SUM(p.amount_minor)<>0 LIMIT 100""").fetchall()
    return {'balanced':not bool(bad),'bad_journal_ids':[r['id'] for r in bad],
            'notice':'Basic arithmetic check only; not an independent financial or security audit.'}
