# Patent Radar

이차전지·반도체 뉴스를 모아 AI 브리핑을 만들고, 한·미·중 특허를 찾아 한국어로 요약하는 개인용 도구.

## 대화 규칙

- 사용자에게 보내는 모든 답변과 보고는 **한국어**로 쓴다.
- 사용자는 개발 초보다. 전문 용어는 짧게 풀어서 설명한다.

## 작업 방법

- Python 명령은 프로젝트 루트에서 `.venv\Scripts\python.exe`로 실행한다. 예: `.venv\Scripts\python.exe -m patent_radar news`
- 테스트: `.venv\Scripts\python.exe -m pytest -q`
- 오늘 브리핑은 `.claude/skills/daily-briefing/SKILL.md`의 순서를 따른다.
- `.env`(API 키)와 `data/`(수집 데이터)는 GitHub에 올리지 않는다.
