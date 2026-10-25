"""Sensor-placement classifiers in analysis/deployment_patterns.py."""
import deployment_patterns as dp


def test_classify_chennai():
    assert dp.classify_chennai("Ganesapuram Subway") == "Road underpass"
    assert dp.classify_chennai("Otteri Nullah bridge") == "Canal / drain"
    assert dp.classify_chennai("Virugambakkam Canal") == "Canal / drain"
    assert dp.classify_chennai("Velachery main road") == "Other low-lying"


def test_classify_pune_drain_lake_river_locality():
    assert dp.classify_pune("s", "Ambil Odha, Pune") == "Nala / drain / lake"
    assert dp.classify_pune("s", "Katraj Talav") == "Nala / drain / lake"
    assert dp.classify_pune("s", "Holkar Bridge") == "River (bridge gauge)"
    assert dp.classify_pune("s", "Mutha riverbank") == "River (bridge gauge)"
    assert dp.classify_pune("s", "Hadapsar gaon") == "Locality hotspot"
    assert dp.classify_pune("s", None) == "Locality hotspot"


def test_classify_pune_samshanbhumi_is_a_river_site():
    # The code comment names "Kharadi Samshanbhumi" as a Mula/Mutha river site
    # whose address omits the river word; the regex spelled it 'samsham'.
    assert dp.classify_pune("s", "Kharadi Samshanbhumi, Pune") == "River (bridge gauge)"
