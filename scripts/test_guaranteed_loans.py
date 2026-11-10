import unittest
from datetime import date
from guaranteed_loans import iso, parse_notice, allowed, collect_source, Fetcher, Document

class LoanTests(unittest.TestCase):
    def test_official_notice_fields_and_approval_condition(self):
        text='借入金（政府保証付）の入札について\n入札予定日 令和8年11月16日\n借入予定額 55,000,000,000円\n実行日 令和8年12月4日\n最終返済日 令和9年12月6日\n財務省の承認を条件として政府保証付借入となる'
        r=parse_notice('jogmec',text,'https://www.jogmec.go.jp/content/notice.pdf','令和8年度12月期資金調達について')
        self.assertEqual((r['date'],r['amount'],r['borrowingDate'],r['maturityDate']),('2026-11-16','550億円','2026-12-04','2027-12-06'))
        self.assertTrue(r['guaranteeConditional'])
    def test_never_use_publication_date_or_application_deadline(self):
        for text in ['令和8年11月1日 政府保証付借入の参加申込期限 令和8年11月12日','政府保証債 発行予定日 令和8年11月16日','政府保証なし 入札予定日 令和8年11月16日']:
            with self.assertRaises(ValueError):parse_notice('jogmec',text,'https://www.jogmec.go.jp/content/x.pdf')
    def test_ambiguous_and_invalid_dates_fail_closed(self):
        with self.assertRaises(ValueError):iso('令和8年2月30日')
        with self.assertRaises(ValueError):parse_notice('pfi','政府保証付 入札日2026年11月1日 入札日2026年11月2日','https://www.pfipcj.co.jp/finance/test/')
    def test_only_official_urls(self):
        for url in ['https://www.jogmec.go.jp.evil.test/content/x.pdf','http://www.jogmec.go.jp/content/x.pdf','https://user@www.jogmec.go.jp/content/x.pdf','https://www.jogmec.go.jp:444/content/x.pdf']:
            self.assertFalse(allowed(url,'jogmec'))
        self.assertFalse(allowed('https://cdn.prod.website-files.com/other/doc.pdf','jic'))
    def test_minto_non_guaranteed_section_is_ignored(self):
        class Mock:
            def doc(self,*_):
                html='<html><body><h2>政府保証借入金</h2><h3>入札情報</h3>現在、掲載情報はありません。<h3>入札結果</h3><h2>民間借入金（政府保証なし）</h2><a href="/assets/pdf/private.pdf">借入入札</a></body></html>'
                self.html=html;return Document(html).text,Document(html)
            def get(self,*_):return self.html.encode()
        self.assertEqual(collect_source('minto',Mock(),date(2026,11,10)),[])

if __name__=='__main__':unittest.main()
