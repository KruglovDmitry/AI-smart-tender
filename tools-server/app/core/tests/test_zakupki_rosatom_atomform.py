# -*- coding: utf-8 -*-
"""Unit tests for Rosatom AtomForm parsers — no network."""

from __future__ import annotations

from app.agent.tools import rosatom as af


def _list_payload(rows: list[dict]) -> dict:
    return {
        "body": {
            "response": {
                "Действия": [
                    {
                        "Данные": {
                            "ГА_ЖурналЗакупокНаСайте": rows,
                            "с_ПараметрыСписков": {
                                "ГА_ЖурналЗакупокНаСайте": {
                                    "ВсегоСтрок": 100,
                                    "СтрокНаСтранице": 50,
                                    "ВсегоСтраниц": 2,
                                }
                            },
                        }
                    }
                ]
            }
        }
    }


def test_rows_and_card_ref() -> None:
    row = {
        "с_Ид": 0,
        "Номер": {"Представление": "248356"},
        "ПредметДоговораЗПРус": {"Представление": "Поставка канцтоваров"},
        "с_Ссылка": {
            "Вид": "ГА_ЗакупочнаяПроцедура",
            "Ид": "83b53c5e-bcc7-11f1-8141-fa163e13b9e9",
            "Тип": "Документ",
            "Представление": "Закупочная процедура №248356",
        },
    }
    payload = _list_payload([row])
    rows = af.rows_from_getlink(payload)
    assert len(rows) == 1
    ref = af.card_from_row(row)
    assert ref is not None
    assert ref["tender_id"] == "248356"
    assert "number=248356" in ref["url"]
    assert "procId=83b53c5e" in ref["url"]
    assert ref["title"] and "канцтоваров" in ref["title"]
    assert af.list_summary(payload)["total_rows"] == 100


def test_parse_card_url() -> None:
    n, p = af.parse_card_url(
        "https://zakupki.rosatom.ru/?link=procurements&number=248356&procId=abc-uuid"
    )
    assert n == "248356"
    assert p == "abc-uuid"


def test_docs_from_card() -> None:
    payload = {
        "body": {
            "response": {
                "Действия": [
                    {
                        "ЗаголовокОкна": "Закупочная процедура №248356",
                        "Данные": {
                            "АктивнаяВерсия.НомерЗПНаСайтеЗакупокГК": {
                                "Представление": "248356"
                            },
                            "АктивнаяВерсия.НМЦЛотов": {"Представление": "100,00"},
                            "ТаблицаФайлов": [
                                {
                                    "ТаблицаФайлов.НаименованиеФайла": {
                                        "Представление": "ТЗ.pdf"
                                    },
                                    "ТаблицаФайлов.ТипФайла": {
                                        "Представление": "Извещение"
                                    },
                                    "ТаблицаФайлов.Ссылка": {
                                        "Ид": "file-uuid-1",
                                        "Тип": "Справочник",
                                    },
                                }
                            ],
                            "ТаблицаФайловИзвещения": [],
                            "ТаблицаПозиций": [
                                {
                                    "ТаблицаПозиций.НаименованиеЛота": {
                                        "Представление": "Лот 1"
                                    },
                                    "ТаблицаПозиций.НМЦ": {"Представление": "100,00"},
                                }
                            ],
                        },
                    }
                ]
            }
        }
    }
    overview = af.overview_from_card(payload)
    assert overview["fields"]["number"] == "248356"
    assert overview["lots"][0]["name"] == "Лот 1"
    docs = af.docs_from_card(payload)
    assert len(docs) == 1
    assert docs[0]["name"] == "ТЗ.pdf"
    assert "rosatomFileId=file-uuid-1" in docs[0]["url"]


def test_minio_from_payload() -> None:
    payload = {
        "body": {
            "response": {
                "Действия": [
                    {
                        "Данные": {
                            "НС_ИмяФайлаМинио": "Закупка.zip",
                            "НС_ПутьФайлаМинио": "/ГА_ОбщееХранилищеФайлов/x/Закупка.zip",
                        }
                    }
                ]
            }
        }
    }
    name, path = af.minio_from_payload(payload)
    assert name == "Закупка.zip"
    assert path and path.endswith("Закупка.zip")
