import copy
import io
import json
import math
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import providers
import rendering
import server
import storage
import workflow


class ChainTests(unittest.TestCase):
    def setUp(self):
        temp_root = storage.ROOT / 'data' / 'test-runs'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.directory = patch.object(storage, 'DATA', Path(self.temp.name))
        self.directory.start()
        self.a = workflow.new_article({'topic':'Рабочее место в квартире', 'length':3000, 'image_count':1, 'format':'guide'})
        self.a.update(title='Рабочее место дома', blocks=[{'id':'b1','markdown':'Оставьте этот абзац **без изменений**.'},
                                                       {'id':'b2','markdown':'Начало абзаца. Важно отметить, что стол нужен. Конец абзаца.'},
                                                       {'id':'b3','markdown':'Последний абзац.'}])
        workflow.version(self.a, 'Черновик')

    def tearDown(self):
        self.directory.stop()
        self.temp.cleanup()

    def test_patch_changes_only_detected_span(self):
        before = copy.deepcopy(self.a['blocks'])
        with patch('workflow.Ollama'), patch('workflow.rewrite_raw', return_value='Стол нужен.'):
            workflow.patch_detected(self.a, ['Важно отметить, что стол нужен.'], storage.DEFAULTS)
        self.assertEqual(self.a['blocks'][0], before[0])
        self.assertEqual(self.a['blocks'][2], before[2])
        self.assertEqual(self.a['blocks'][1]['markdown'], 'Начало абзаца. Стол нужен. Конец абзаца.')
        self.assertIsNone(self.a['check'])
        self.assertEqual(self.a['version'], 2)

    def test_preserves_bold_markup(self):
        self.a['blocks'][1]['markdown'] = 'Начало **шаблонный текст** конец.'
        with patch('workflow.Ollama'), patch('workflow.rewrite_raw', return_value='живой текст'):
            workflow.patch_detected(self.a, ['шаблонный текст'], storage.DEFAULTS)
        self.assertEqual(self.a['blocks'][1]['markdown'], 'Начало **живой текст** конец.')

    def test_detector_can_flag_title(self):
        before=copy.deepcopy(self.a['blocks'])
        with patch('workflow.Ollama'), patch('workflow.rewrite_raw',return_value='Удобный стол дома'):
            workflow.patch_detected(self.a,['Рабочее место дома'],storage.DEFAULTS)
        self.assertEqual(self.a['title'],'Удобный стол дома')
        self.assertEqual(self.a['blocks'],before)

    def test_patch_batch_is_atomic_on_model_failure(self):
        before = copy.deepcopy(self.a['blocks'])
        with patch('workflow.Ollama'), patch('workflow.rewrite_raw', side_effect=['Начало.', providers.ProviderError('Ошибка')]):
            with self.assertRaises(providers.ProviderError):
                workflow.patch_detected(self.a, ['Начало абзаца.', 'Конец абзаца.'], storage.DEFAULTS)
        self.assertEqual(self.a['blocks'], before)

    def test_rejects_ambiguous_and_missing_highlights(self):
        for quote in ['абзац', 'Несуществующая цитата']:
            with self.assertRaises(ValueError):
                workflow.map_fragments(self.a, [quote])

    def test_rejects_overlap(self):
        with self.assertRaises(ValueError):
            workflow.map_fragments(self.a, ['Важно отметить, что стол нужен.', 'что стол нужен.'])

    def test_stale_report_rejected(self):
        old = workflow.digest(self.a)
        self.a['blocks'][0]['markdown'] += ' Новая фраза.'
        with self.assertRaises(ValueError):
            workflow.attach_report(self.a, {'score':2}, old)
        self.assertIsNone(self.a['check'])

    def test_threshold_boundary(self):
        report=workflow.attach_report(self.a, {'score':10, 'fragments':[], 'provider':'test'}, workflow.digest(self.a))
        workflow.detection_loop(self.a, storage.DEFAULTS, report)
        self.assertEqual(self.a['state'], 'checked')

    def test_over_threshold_without_fragments_does_not_rewrite(self):
        before=copy.deepcopy(self.a['blocks'])
        report=workflow.attach_report(self.a, {'score':10.01, 'fragments':[], 'provider':'test'}, workflow.digest(self.a))
        workflow.detection_loop(self.a, storage.DEFAULTS, report)
        self.assertEqual(self.a['state'],'needs_fragments')
        self.assertEqual(before,self.a['blocks'])

    def test_loop_cap_prevents_unbounded_rewrites(self):
        self.a['revision_count']=3
        workflow.detection_loop(self.a, storage.DEFAULTS, {'score':90, 'fragments':['Конец абзаца.']})
        self.assertEqual(self.a['state'],'needs_review')

    def test_manual_patch_requires_fresh_check(self):
        report=workflow.attach_report(self.a, {'score':90, 'fragments':['Конец абзаца.'], 'provider':'test'}, workflow.digest(self.a))
        with patch('workflow.Ollama'), patch('workflow.rewrite_raw', return_value='Завершение абзаца.'):
            workflow.detection_loop(self.a, storage.DEFAULTS, report)
        self.assertEqual(self.a['state'],'awaiting_check')
        self.assertIsNone(self.a['check'])
        self.assertEqual(len(self.a['reports']),1)

    def test_api_loop_rechecks_mutated_document(self):
        config={**storage.DEFAULTS,'detector':'zerogpt'}
        first={'score':80,'fragments':['Конец абзаца.'],'provider':'test'}
        second={'score':4,'fragments':[],'provider':'test'}
        with patch('workflow.detect',side_effect=[first,second]) as detect, patch('workflow.Ollama'), patch('workflow.rewrite_raw',return_value='Завершение абзаца.'):
            workflow.detection_loop(self.a,config)
        self.assertEqual(self.a['state'],'checked')
        self.assertEqual(self.a['check']['version'],2)
        self.assertNotEqual(detect.call_args_list[0].args[0],detect.call_args_list[1].args[0])

    def test_bad_detector_results_never_become_zero(self):
        config={**storage.DEFAULTS,'zerogpt_token':'secret','zerogpt_key':'secret'}
        for result in [{'success':False},{'success':True,'data':{}},{'success':True,'data':{'fakePercentage':math.nan}}]:
            with patch('providers.request_json',return_value=result), self.assertRaises(providers.ProviderError):
                providers.detect('Текст',config)

    def test_invalid_manual_scores(self):
        for score in [-1,101,math.nan,math.inf,True,None]:
            with self.assertRaises((ValueError,TypeError)):
                providers.validate_report(score,[])

    def test_rewriter_cannot_append_context(self):
        quote='Важно отметить, что стол нужен.'
        context=quote+' Соседний текст, который должен остаться на своём месте без копирования в новую замену.'
        with patch('providers.Ollama.call',return_value={'replacement':'Стол нужен. '+context}):
            with self.assertRaises(providers.ProviderError):
                workflow.rewrite_raw(providers.Ollama(storage.DEFAULTS),quote,context,'Правка')

    def test_rewriter_cannot_repeat_adjacent_sentence(self):
        quote='Сначала освободите стол и проверьте, удобно ли вы сидите за ним в обычном рабочем положении.'
        adjacent='На соседней полке храните бумаги, которыми пользуетесь во время работы.'
        with patch('providers.Ollama.call',return_value={'replacement':adjacent}):
            with self.assertRaises(providers.ProviderError):
                workflow.rewrite_raw(providers.Ollama(storage.DEFAULTS),quote,quote+adjacent,'Правка')

    def test_export_contains_actual_images_and_report(self):
        directory=storage.DATA/'articles'/self.a['id'];directory.mkdir(parents=True)
        (directory/'image-1.png').write_bytes(b'PNG test fixture')
        self.a['images']=[{'filename':'image-1.png','caption':'Иллюстрация'}]
        data=server.export(self.a)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('images/image-1.png',z.namelist())
            self.assertIn('images/image-1.png',z.read('article.md').decode())
            self.assertIn('report.json',z.namelist())

    def test_renderer_escapes_model_html(self):
        output=rendering.render('<script>alert(1)</script>\n\n**Текст**\n\n| A | B |\n|---|---|\n| 1 | 2 |')
        self.assertNotIn('<script>',output)
        self.assertIn('<strong>Текст</strong>',output)
        self.assertIn('<table>',output)

    def test_no_credential_leak_in_status(self):
        with patch('storage.settings',return_value={**storage.DEFAULTS,'zerogpt_key':'secret','zerogpt_token':'jwt'}), patch('server.request_json',return_value={'models':[]}):
            self.assertNotIn('secret',json.dumps(server.status()))
            self.assertNotIn('jwt',json.dumps(server.status()))

    def test_local_provider_address_guard(self):
        for address in ['http://example.com','file:///tmp/foo','http://127.0.0.1@evil.com','https://localhost:1']:
            with self.assertRaises(ValueError):server.local_url(address)
        self.assertEqual(server.local_url('http://127.0.0.1:11434'),'http://127.0.0.1:11434')


if __name__ == '__main__':
    unittest.main()
