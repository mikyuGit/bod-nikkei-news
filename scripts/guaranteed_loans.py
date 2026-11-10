"""Public government-guaranteed loan notices; no securities or procurement tenders.
PDF extraction runs on a standard public GitHub runner, never inside the Worker.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

SOURCES = {
    'ndf': ('原子力損害賠償・廃炉等支援機構', 'https://www.ndf.go.jp/capital/ggl/ggl_index/'),
    'jic': ('産業革新投資機構', 'https://www.j-ic.co.jp/jp/finance/about'),
    'jogmec': ('JOGMEC', 'https://www.jogmec.go.jp/bid/finance/{fy}.html'),
    'pfi': ('民間資金等活用事業推進機構', 'https://www.pfipcj.co.jp/finance/'),
    'occto': ('電力広域的運営推進機関', 'https://www.occto.or.jp/seifuhosyoukariire/'),
    'minto': ('民間都市開発推進機構', 'https://www.minto.or.jp/ir/borrowing/'),
}
WORKER = 'https://discord-jgb-auction-bot.mikyu38.workers.dev/auction/ingest/guaranteed'
CDN_PREFIX = '/699cba437b4e7de2649d32c0/'
UA = 'BoD-Calendar-Collector/1.0 (+https://github.com/mikyuGit/bod-nikkei-news)'
DAY = r'(?:(?:令和|平成)(?:元|\d{1,2})|20\d{2})年\d{1,2}月\d{1,2}日'

def compact(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s))

def iso(s):
    m = re.fullmatch(r'(令和|平成)?(元|\d{1,4})年(\d{1,2})月(\d{1,2})日', compact(s))
    if not m:
        raise ValueError('Unrecognized date: ' + s[:80])
    y = (1 if m[2] == '元' else int(m[2])) + {'令和': 2018, '平成': 1988, None: 0}[m[1]]
    return date(y, int(m[3]), int(m[4])).isoformat()

def fiscal_year(text):
    m = re.search(r'(令和|平成)?(元|\d{1,4})年度', compact(text))
    if not m:
        raise ValueError('Fiscal year missing')
    return (1 if m[2] == '元' else int(m[2])) + {'令和': 2018, '平成': 1988, None: 0}[m[1]]

def allowed(url, source):
    u = urllib.parse.urlsplit(url)
    origin = urllib.parse.urlsplit(SOURCES[source][1])
    return (u.scheme == 'https' and not u.username and not u.password and not u.port
            and len(url) <= 1800 and not u.fragment and (
                (u.hostname == origin.hostname and (u.path.startswith(origin.path.rsplit('/', 1)[0])
                 or (source == 'ndf' and u.path.startswith('/capital/ggl/'))
                 or (source == 'jogmec' and u.path.startswith('/content/'))
                 or (source == 'pfi' and u.path.startswith('/assets/dl/finance/'))
                 or (source == 'occto' and u.path.startswith(('/assets/seifuhosyoukariire/', '/_include/json/')))
                 or (source == 'minto' and u.path.startswith('/assets/pdf/'))))
                or (source == 'jic' and u.hostname == 'cdn.prod.website-files.com' and u.path.startswith(CDN_PREFIX))))

class Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []
    @property
    def text(self):
        return ''.join(c if isinstance(c, str) else c.text for c in self.children)
    def all(self, tag):
        for c in self.children:
            if isinstance(c, Node):
                if c.tag == tag:
                    yield c
                yield from c.all(tag)

class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node(); self.stack = [self.root]
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs); self.stack[-1].children.append(n)
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append(n)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]; break
    def handle_data(self, s):
        if self.stack[-1].tag not in {'script','style'}:
            self.stack[-1].children.append(s)
    @property
    def text(self): return self.root.text
    def links(self, base):
        for a in self.root.all('a'):
            href = a.attrs.get('href')
            if href and not href.startswith('#'):
                yield compact(a.text), urllib.parse.urljoin(base, href)

def record(source, day, url, amount=None, borrowing=None, maturity=None, label=None, conditional=False):
    if not allowed(url, source):
        raise ValueError('Non-official notice URL')
    if borrowing and borrowing < day:
        raise ValueError('Borrowing precedes auction')
    if maturity and borrowing and maturity <= borrowing:
        raise ValueError('Maturity precedes borrowing')
    return {'date': day, 'name': SOURCES[source][0] + (' ' + label if label else ''), 'planURL': url,
            'amount': amount, 'borrowingDate': borrowing, 'maturityDate': maturity,
            'guaranteeConditional': conditional}

def parse_notice(source, text, url, title=''):
    t = compact(text)
    if not re.search(r'政府保証(?:付|付き|借入)', t):
        raise ValueError('Government guarantee not confirmed')
    days = {iso(m[1]) for m in re.finditer(r'(?:入札予定日|入札期日|入札の実施予定日|入札実施日|入札日)(?:および方法)?[：:]?(' + DAY + ')', t)}
    if len(days) != 1:
        raise ValueError('Unambiguous auction day missing')
    def field(pattern):
        m = re.search(pattern + r'[：:]?(' + DAY + ')', t)
        return iso(m[1]) if m else None
    borrowing = field(r'(?:借入予定日|借入日|実行日)')
    maturity = field(r'(?:返済予定日|最終返済日|償還期限(?:及び方法)?)')
    amount = re.search(r'借入予定額[：:]?([\d,]+(?:兆[\d,]*)?(?:億[\d,]*)?(?:万[\d,]*)?円(?:程度)?)', t)
    label = None
    period = re.search(r'(?:令和\d+|20\d+)年度(\d+月期)', compact(title))
    round_ = re.search(r'第\d+回', compact(title))
    if period: label = period[1]
    elif round_: label = round_[0]
    amount_text = amount[1] if amount else None
    if amount_text and re.fullmatch(r'[\d,]+円',amount_text):
        yen=int(amount_text[:-1].replace(',',''))
        if yen % 100_000_000 == 0: amount_text=f'{yen//100_000_000:,}億円'
    return record(source, next(iter(days)), url, amount_text, borrowing, maturity, label,
                  bool(re.search(r'(?:承認|保証).{0,10}条件|条件.{0,10}政府保証', t)))

class Fetcher:
    def __init__(self, fixtures=None):
        self.count = 0; self.cache = {}; self.fixtures = Path(fixtures) if fixtures else None
        self.manifest = json.loads((self.fixtures/'manifest.json').read_text()) if fixtures else {}
    def get(self, url, source):
        if not allowed(url, source): raise ValueError('URL outside official source')
        if url in self.cache: return self.cache[url]
        if self.fixtures:
            value = (self.fixtures/self.manifest[url]).read_bytes()
        else:
            self.count += 1
            if self.count > 36: raise ValueError('Collector request budget reached')
            class Redirects(urllib.request.HTTPRedirectHandler):
                max_redirections = 3
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    if not allowed(newurl, source): raise ValueError('Unexpected redirect')
                    return super().redirect_request(req, fp, code, msg, headers, newurl)
            for attempt in range(2):
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,application/pdf,application/json'})
                    with urllib.request.build_opener(Redirects()).open(req, timeout=20) as response:
                        value = response.read(2*1024*1024+1)
                    break
                except urllib.error.HTTPError as e:
                    retryable = e.code in (429,500,502,503,504); status = e.code; e.close()
                    if not retryable or attempt: raise ValueError('Official source HTTP ' + str(status)) from None
                except (urllib.error.URLError, TimeoutError):
                    if attempt: raise
                time.sleep(10)
        if len(value) > 2*1024*1024: raise ValueError('Official document exceeds 2 MiB')
        self.cache[url] = value
        return value
    def doc(self, url, source):
        data = self.get(url, source)
        if data.startswith(b'%PDF'):
            with tempfile.TemporaryDirectory() as directory:
                p = Path(directory)/'notice.pdf'; p.write_bytes(data)
                result = subprocess.run(['pdftotext','-f','1','-l','5','-layout',str(p),'-'], capture_output=True, check=True, timeout=15)
                text = result.stdout.decode('utf-8', errors='strict')
                if not text.strip() or len(text) > 100_000: raise ValueError('PDF text could not be verified')
                return text, None
        text = data.decode('utf-8-sig', errors='strict')
        if '<html' not in text.lower() or '<body' not in text.lower(): raise ValueError('Expected official HTML page')
        doc = Document(text)
        return doc.text, doc

def recent(day, today):
    return today - timedelta(days=7) <= date.fromisoformat(day) <= today + timedelta(days=730)

def collect_source(source, fetcher, today):
    fy = today.year - (today.month < 4)
    base = SOURCES[source][1].format(fy=fy)
    text, doc = fetcher.doc(base, source)
    if source == 'ndf':
        records = []
        year = fiscal_year(text)
        for table in doc.root.all('table'):
            if not all(x in compact(table.text) for x in ['入札時期','借入日','借入額']): continue
            for tr in table.all('tr'):
                cells = [compact(c.text) for c in tr.children if isinstance(c,Node) and c.tag in ('td','th')]
                if len(cells) != 5 or not re.fullmatch(r'第\d+回',cells[0]): continue
                def day(value):
                    m = re.fullmatch(r'(\d{1,2})月(\d{1,2})日',value)
                    if not m: raise ValueError('NDF table day missing')
                    return date(year+(int(m[1])<4),int(m[1]),int(m[2])).isoformat()
                auction = day(cells[1]); borrow = day(cells[2])
                if not recent(auction,today): continue
                notice = next((u for t,u in doc.links(base) if cells[0] in t and '入札について' in t and str(year-2018)+'年度' in t),base)
                if notice != base:
                    detail,_ = fetcher.doc(notice,source)
                    r = parse_notice(source,detail,notice,cells[0])
                    if r['date'] != auction: raise ValueError('NDF schedule and notice disagree')
                else: r = record(source,auction,base,cells[4],borrow,label=cells[0])
                records.append(r)
        if '入札時期' not in compact(text): raise ValueError('NDF schedule table missing')
        return records
    if source == 'minto':
        # This site explicitly separates guaranteed and non-guaranteed borrowing.
        t = compact(text)
        if '政府保証借入金' not in t or '入札情報' not in t or '入札結果' not in t: raise ValueError('MINTO guaranteed section missing')
        raw = fetcher.get(base,source).decode('utf-8-sig')
        section = re.split(r'入札情報', raw, maxsplit=1)[1].split('入札結果',1)[0]
        if '現在、掲載情報はありません' in compact(Document(section).text): return []
        links = list(Document(section).links(base))
        if not links: raise ValueError('MINTO notice section not recognized')
    elif source == 'occto':
        json_path = next((n.attrs.get('data-newslist') for n in doc.root.all('div')
                          if 'funding-list-seifuhosyoukariire.json' in n.attrs.get('data-newslist','')),None)
        if not json_path: raise ValueError('OCCTO borrowing index missing')
        data = json.loads(fetcher.get(urllib.parse.urljoin(base,json_path),source))
        links = [(compact(r['title']),urllib.parse.urljoin(base,r['url'])) for r in data]
    else: links = list(doc.links(base))
    candidates = []
    for title,url in links:
        if re.search(r'結果|参加|募集|委託|政府保証債',title): continue
        match = {'jic': r'入札概要.*入札分', 'jogmec': r'年度\d+月期資金調達について',
                 'pfi': r'政府保証借入の入札について', 'occto': r'資金借入.*入札について',
                 'minto': r'借入|入札'}[source]
        if not re.search(match,title): continue
        dates = re.findall(DAY,title)
        if dates and not recent(iso(dates[-1]),today): continue
        fy_match = re.search(r'(?:令和\d+|20\d+)年度',title)
        if fy_match and fiscal_year(title) < fy: continue
        if (title,url) not in candidates: candidates.append((title,url))
    if len(candidates)>8: raise ValueError('Too many current loan notices; check source structure')
    records=[]
    for title,url in candidates:
        detail,subdoc = fetcher.doc(url,source)
        if subdoc:
            # Follow actual document links from the notice only; never manufacture a PDF URL.
            pdfs=[u for t,u in subdoc.links(url) if urllib.parse.urlsplit(u).path.lower().endswith('.pdf')
                  and not re.search('結果|参加|募集|委託',t) and re.search('入札|借入',t)]
            pdfs=list(dict.fromkeys(pdfs))
            if len(pdfs)>4: raise ValueError('Ambiguous loan notice attachments')
            if pdfs:
                for pdf in pdfs:
                    body,_ = fetcher.doc(pdf,source)
                    r=parse_notice(source,body,pdf,title)
                    if recent(r['date'],today): records.append(r)
                continue
        r=parse_notice(source,detail,url,title)
        if recent(r['date'],today): records.append(r)
    if source == 'jic':
        # Annual schedule gives advance dates even before a detailed notice exists.
        annual=[]
        for title,url in links:
            if '借入金予定' not in title or fiscal_year(title) < fy: continue
            schedule,_=fetcher.doc(url,source)
            t=compact(schedule); year=fiscal_year(t)
            if '政府保証付' not in t or '入札時期' not in t: raise ValueError('JIC annual schedule not recognized')
            matches=list(re.finditer(r'(第\d+回)(\d{1,2}月\d{1,2}日)(\d{1,2}月\d{1,2}日)(?:\d+(?:年|ヶ月|か月))([\d,]+億円)',t))
            if not matches: raise ValueError('JIC annual schedule rows missing')
            def annual_day(value):
                month=int(value.split('月')[0]);return iso(str(year+(month<4))+'年'+value)
            for m in matches:
                day=annual_day(m[2])
                if recent(day,today): annual.append(record(source,day,url,m[4],annual_day(m[3]),label=m[1]))
        byday={r['date']:r for r in records}
        for planned in annual:
            detailed=byday.get(planned['date'])
            byday[planned['date']]={**planned,**detailed,'name':planned['name']} if detailed else planned
        records=list(byday.values())
    # A recognized section with no current notices is a successful empty snapshot.
    markers={'jic':'政府保証借入','jogmec':'資金調達','pfi':'資金調達','occto':'政府保証借入','minto':'政府保証借入金'}
    if markers[source] not in compact(text): raise ValueError('Official loan section missing')
    unique={}
    for r in records:
        key=(r['date'],r['planURL'])
        if key in unique and unique[key]!=r: raise ValueError('Conflicting loan notices')
        unique[key]=r
    return sorted(unique.values(),key=lambda r:(r['date'],r['planURL']))

def identity(audience):
    target=os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    u=urllib.parse.urlsplit(target)
    if u.scheme!='https' or not u.hostname.endswith('.actions.githubusercontent.com'): raise ValueError('Invalid OIDC endpoint')
    target+=('&' if u.query else '?')+urllib.parse.urlencode({'audience':audience})
    req=urllib.request.Request(target,headers={'Authorization':'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    class NoRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args): raise ValueError('Unexpected identity/Worker redirect')
    opener=urllib.request.build_opener(NoRedirects())
    with opener.open(req,timeout=20) as r: token=json.loads(r.read(65536))['value']
    if not isinstance(token,str) or len(token)>18000 or token.count('.')!=2: raise ValueError('Invalid OIDC token')
    return token,opener

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dry-run',action='store_true');parser.add_argument('--fixtures');parser.add_argument('--date')
    args=parser.parse_args()
    today=date.fromisoformat(args.date) if args.date else datetime.now(timezone(timedelta(hours=9))).date()
    fetcher=Fetcher(args.fixtures);sources=[]
    for source in SOURCES:
        try:
            records=collect_source(source,fetcher,today)
            sources.append({'source':source,'ok':True,'records':records})
        except Exception as e:
            sources.append({'source':source,'ok':False,'error':str(e)[:240]})
    payload={'version':1,'collected_at':datetime.now(timezone.utc).isoformat(),'sources':sources}
    if not args.dry_run:
        token,opener=identity(WORKER)
        body=json.dumps(payload,ensure_ascii=False).encode()
        if len(body)>65536: raise ValueError('Snapshot too large')
        for attempt in range(2):
            try:
                req=urllib.request.Request(WORKER,data=body,method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':UA})
                with opener.open(req,timeout=30) as r: result=json.loads(r.read(65536))
                if not result.get('ok'): raise ValueError('Worker rejected snapshot')
                payload['worker']=result;break
            except urllib.error.HTTPError as e:
                status=e.code;message=e.read(1024).decode(errors='replace');e.close()
                if attempt or status not in (429,500,502,503,504): raise ValueError('Worker HTTP '+str(status)+': '+message) from None
                time.sleep(10)
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return 0 if all(s['ok'] for s in sources) else 1

if __name__=='__main__':
    sys.exit(main())
