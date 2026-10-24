import unittest

from recognition import RecognitionEngine


class RecommendationContentTests(unittest.TestCase):
    def test_generic_book_cards_do_not_enter_split_chapter_body(self):
        # Minimal reproduction of bqquge's .con / sibling recommendation list;
        # include ordinary prose mentioning a recommended title to guard against
        # removing book names by a global word blacklist.
        paragraphs = ['接下来的几天，江枫都在练习八宝栗香鸽。',
                      '他提到了第一序列这本书，然后继续练习，准备参加明天的厨艺比赛。',
                      '「有的。」']
        cards = ''.join(f'<li><a href="/{i}"><img></a><p><a href="/{i}">{name}</a></p></li>'
                        for i, name in enumerate(['第一序列', '猜你喜欢中的另一部小说', '那年花开1981']))
        for suffix, label, target in [('', '下一页', '676855-2'),
                                      ('-4', '下一章', '676856')]:
            with self.subTest(suffix=suffix):
                html = ('<body><div class="container"><div class="con"><h1>第一百四十八章 良心店铺</h1>'
                        + ''.join(f'<p>{text}</p>' for text in paragraphs)
                        + f'</div><div class="prenext"><a href="/671#dir">目录</a>'
                        f'<a href="/671/{target}">{label}</a></div></div>'
                        f'<div class="container"><h2>猜你喜欢</h2><ul class="list clear">{cards}</ul></div></body>')
                result = RecognitionEngine().analyze_html(html, f'https://www.bqquge.org/671/676855{suffix}')
                self.assertEqual(result.content, paragraphs)
                self.assertEqual(result.toc_url, 'https://www.bqquge.org/671#dir')
                if suffix:
                    self.assertEqual(result.next_url, 'https://www.bqquge.org/671/676856')
                    self.assertIsNone(result.next_page_url)
                else:
                    self.assertEqual(result.next_page_url, 'https://www.bqquge.org/671/676855-2')
                    self.assertIsNone(result.next_url)


if __name__ == '__main__':
    unittest.main()
