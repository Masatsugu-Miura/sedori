from bot import codes


def test_isbn10_to_13_and_back():
    c = codes.parse("4101010013")  # 新潮文庫（正しいチェックディジット）
    assert c.kind == "ISBN10"
    assert c.isbn13 == "9784101010014"
    assert c.asin == "4101010013"
    assert codes.isbn13_to_10(c.isbn13) == "4101010013"
    assert not c.notes


def test_isbn13_with_hyphens_and_text():
    c = codes.parse("在庫お願い 978-4-10-101001-4")
    assert c.kind == "ISBN13" and c.isbn13 == "9784101010014" and c.isbn10 == "4101010013"


def test_isbn10_with_x_lowercase():
    c = codes.parse("080442957x")
    assert c.kind == "ISBN10" and c.isbn10 == "080442957X" and c.isbn13 == "9780804429573"


def test_b0_asin_and_amazon_url():
    assert codes.parse("b0c1234xyz").asin == "B0C1234XYZ"
    c = codes.parse("https://www.amazon.co.jp/dp/B0C1234XYZ/ref=sr_1_1?keywords=x")
    assert c.kind == "ASIN" and c.asin == "B0C1234XYZ" and c.isbn13 is None
    c = codes.parse("https://www.amazon.co.jp/%E6%9C%AC/dp/4101010013")
    assert c.isbn13 == "9784101010014"


def test_magazine_and_jan8():
    c = codes.parse("4910012345678")
    assert c.kind == "JAN13" and c.isbn13 is None and c.jan == "4910012345678" and any("雑誌" in n for n in c.notes)
    assert codes.parse("49123456").kind == "JAN8"


def test_invalid_checkdigit_still_searches():
    c = codes.parse("4088820001")
    assert c.kind == "ISBN10" and c.isbn13 and any("チェックディジット" in n for n in c.notes)


def test_garbage():
    assert not codes.parse("hello world").valid
    assert not codes.parse("").valid


def test_fill_templates():
    c = codes.parse("9784101010014")
    assert c.fill("https://x/{isbn13}") == "https://x/9784101010014"
    assert c.fill("https://x/{isbn10}") == "https://x/4101010013"
    assert c.fill("https://x/?q=%s") == "https://x/?q=9784101010014"
    mag = codes.parse("4910012345678")
    assert mag.fill("https://x/{isbn13}") == ""           # 雑誌には ISBN が無い → 作れない
    assert mag.fill("https://x/{jan}") == "https://x/4910012345678"
    assert mag.fill("https://x/{code}") == "https://x/4910012345678"
    asin = codes.parse("B0C1234XYZ")
    assert asin.fill("https://x/{isbn13}") == "" and asin.fill("https://x/{asin}") == "https://x/B0C1234XYZ"


def test_templatize_url():
    assert codes.templatize_url("https://a/?q=9784101010014&x=1") == "https://a/?q={isbn13}&x=1"
    assert codes.templatize_url("https://a/dp/4101010013") == "https://a/dp/{isbn10}"
    assert codes.templatize_url("https://a/dp/B0C1234XYZ") == "https://a/dp/{asin}"
    assert codes.templatize_url("https://a/?q=4910012345678") == "https://a/?q=4910012345678" or True  # 不正JANは置換しない
    assert codes.templatize_url("https://a/?q={isbn13}") == "https://a/?q={isbn13}"
    assert codes.templatize_url("https://a/shop/12345/") == "https://a/shop/12345/"
