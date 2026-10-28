import unittest
from unittest.mock import patch

from browser_detector import parse_visible_report
from providers import ProviderError
import storage
import workflow


class ReportTests(unittest.TestCase):
    def test_ignores_marketing_accuracy_percentage(self):
        with self.assertRaises(ProviderError):
            parse_visible_report('98.4% Detection accuracy',[])

    def test_parses_real_visible_result_shape(self):
        report=parse_visible_report('Your Text contains mixed signals\n32.6%\nAI GPT*\n98.4% Detection accuracy',['Точная цитата.'])
        self.assertEqual(report['score'],32.6)
        self.assertEqual(report['fragments'],['Точная цитата.'])

    def test_zero_is_only_accepted_with_real_result_label(self):
        self.assertEqual(parse_visible_report('0%\nAI GPT*',[])['score'],0)


class BrowserFallbackTests(unittest.TestCase):
    def test_timeout_requires_manual_check_and_has_no_score(self):
        article={'id':'example','events':[],'blocks':[],'title':'Пример','version':1,'check':None}
        with patch('workflow.event'),patch('browser_detector.detect_browser',side_effect=ProviderError('CAPTCHA')):
            workflow.detection_loop(article,{**storage.DEFAULTS,'detector':'browser'})
        self.assertEqual(article['state'],'awaiting_check')
        self.assertIsNone(article['check'])


if __name__=='__main__':unittest.main()
