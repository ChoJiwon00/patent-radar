# Patent Radar

이차전지·반도체 분야의 **최신 뉴스**를 매일 아침 모으고, 뉴스에서 뽑은 기술 키워드로
**한국·미국·중국 특허**를 찾아 **AI가 한국어로 요약**해 주는 개인용 특허 레이더입니다.

![Patent Radar 화면](docs/screenshot.jpg)

## 기능

| 기능 | 상태 |
|---|---|
| 이차전지·반도체 뉴스 자동 수집 (Google News RSS) | ✅ 완료 |
| 이미 본 기사 다시 안 보이기 (같은 링크·비슷한 제목 제거) | ✅ 완료 |
| 웹페이지에서 날짜·주제·제목으로 뉴스 보기 | ✅ 완료 |
| 한국 특허 검색 (KIPRIS Plus) | ⏳ 인증키 발급 대기 |
| 미국·중국 특허 검색 (KIPRIS Plus 해외특허) | ⏳ 예정 |
| 한 번 본 특허 숨기기, 크게 뜨는 특허 🔥 강조 | ⏳ 예정 |
| 청구항 AI 요약과 한국어 번역 (Claude Code 스킬) | ⏳ 예정 |
| 매일 오전 9시 자동 브리핑 | ⏳ 예정 |
| 키워드로 특허 직접 검색 | ⏳ 예정 |

## 동작 방식

```
매일 09:00 ─▶ 뉴스 수집 ─▶ 기술 키워드 추출 ─▶ 한·미·중 특허 검색(최신순)
                                            │
             웹페이지 ◀─ 한국어 요약·번역 ◀─ 본 특허 제외 / 🔥 강조
```

## 폴더 구조

```
patent-radar/
├─ config/topics.toml        뉴스 주제와 검색어 (자유롭게 수정)
├─ patent_radar/
│  ├─ __main__.py            명령줄 진입점
│  ├─ config.py              경로, .env, 설정 읽기
│  ├─ db.py                  SQLite 저장소 (data/radar.db)
│  ├─ news.py                뉴스 수집·중복 제거
│  ├─ web.py                 웹 서버와 API
│  └─ static/                웹페이지 (HTML·CSS·JS)
├─ tests/                    자동 테스트
├─ .env.example              API 키 양식 (.env는 GitHub에 올라가지 않음)
└─ requirements.txt
```

## 설치

Python 3.11 이상이 필요합니다.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

`.env` 파일을 열어 `KIPRIS_API_KEY=` 뒤에 KIPRIS Plus 인증키를 붙여 넣습니다.

## 사용법

```bash
# 뉴스 수집
.venv\Scripts\python -m patent_radar news

# 웹페이지 실행 → 브라우저에서 http://127.0.0.1:8000
.venv\Scripts\python -m patent_radar serve

# 테스트
.venv\Scripts\python -m pytest
```

## 데이터 출처

- 뉴스: [Google News RSS](https://news.google.com) 검색 피드
- 한국·해외 특허: [KIPRIS Plus](https://plus.kipris.or.kr) Open API (무료 월 1,000건)

## 기술 스택

Python 3.13 · FastAPI · SQLite · HTML/CSS/JavaScript · Claude Code
