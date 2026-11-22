---
name: monthly-report
description: Patent Radar의 월간 트렌드 리포트. 끝난 달(기본은 지난달) 한 달 동안의 뉴스·특허 브리핑에서 통계(기사 수, 주차별 흐름, 자주 나온 기술 키워드, 출원인·기술 분류 순위, 가장 오래 주목받은 특허, 지난달과의 비교, KIPRIS 호출 수)를 뽑고, 한 달의 흐름을 한국어로 분석해 claude.ai 페이지·GitHub 보고서·공개 사이트에 올린다. "월간 리포트", "지난달 정리", "monthly report"를 요청받거나 매월 1일 0시 예약 작업에서 실행된다.
allowed-tools: PowerShell(.venv\Scripts\python.exe -m patent_radar *), Edit(data/**), Read(data/**), Read(reports/**), ArtifactData, Bash(git add reports docs), Bash(git diff --cached *), Bash(git commit *), Bash(git pull --rebase --autostash origin main), Bash(git rebase --abort), Bash(git push origin main)
---

# 월간 트렌드 리포트

프로젝트(Patent Radar) 루트 폴더에서 아래 순서대로 진행한다. Python 명령은 `.venv\Scripts\python.exe`로 실행한다.
사용자에게 보내는 말은 모두 한국어로 쓴다. claude.ai 브리핑 페이지 주소는 https://claude.ai/artifact/1BqRNiZ2mYqMX2t9bkegTd 이다.
`ArtifactData` 도구가 보이지 않으면 ToolSearch로 `select:ArtifactData`를 불러온다.

## 1. 통계 뽑기

```
.venv\Scripts\python.exe -m patent_radar monthly stats
```

지난달(매월 1일이면 바로 전 달) 통계가 `data/monthly-stats.json`에 저장된다. 다른 달이면 `--month YYYY-MM`을 붙인다.
"그달 브리핑이 하나도 없어요" 경고가 나오면 달을 잘못 고른 것이니 멈추고 보고한다.
아직 끝나지 않은 달은 저장되지 않는다 (사용자가 꼭 원할 때만 `monthly save`에 `--force`).
파일이 길면 나눠 읽는다. 비교에 쓰는 값(`previous`, `kipris_calls`, `weeks`, `weekly_reports`)은 파일 앞쪽에 있다.

파일에 들어 있는 것:

- `briefings`: 날짜별 뉴스 브리핑의 흐름(`overview`)과 주요 이야기 제목
- `news`: 주제별 기사 수. `by_week`는 `weeks`(월요일~일요일, 그달 안의 날만)와 같은 순서의 주차별 합계
- `keywords`: 자주 나온 기술 키워드(나온 날 수), `patents`: 나라·출원인·기술 분류 통계
- `patents.total`과 나라·출원인·분류 수는 그달에 처음 보여 준 특허만 센다 (앞 달에서 이어진 주목 특허는 빼고)
- `patents.spotlight`: 가장 여러 날 🔥 주목 특허 순위에 오른 특허 10건. `days_hot`은 순위에 오른 날 수, `rank`는 가장 높았던 순위,
  `patents.hot_total`은 그달 주목 특허 전체 수
- `weekly_reports`: 그달 몫 주간 리포트(7일 가운데 4일 이상이 그달)와 그 흐름 제목들
- `previous`: 지난달 브리핑 일수·기사 수·특허 수 (없으면 null), `kipris_calls`: 그달 KIPRIS 호출 수

## 2. 한 달 흐름 분석하기

통계 파일만 보고 쓴다(그달 주간 리포트 `reports\weekly\날짜.md`는 흐름을 확인하는 데만 참고한다).
파일에 없는 사실·숫자·전망은 지어내지 않는다. 문체는 해요체로 쓴다.

- `summary`: 한 달을 통틀어 4~6문장. 세 주제(이차전지 → ESS → 반도체) 순서로 무엇이 한 달 내내 이어졌고 무엇이 새로 나왔는지,
  특허는 어느 기술·출원인에 몰렸는지. `previous`가 있으면 지난달과 기사·특허 수를 한 번 비교한다.
  두 달의 브리핑 일수가 다르면 하루 평균으로 비교하거나 두 달의 일수를 함께 적는다.
  이 도구의 중심이 이차전지(특히 LFP)라서 첫 문장은 이차전지 이야기로 시작한다.
- `trends`: 이번 달 흐름 3~8개. `{"title": 짧은 제목, "body": 2~4문장, "topic": "이차전지"|"ESS"|"반도체"}`.
  여러 주에 걸쳐 이어진 이야기(주간 리포트 흐름 제목이 여러 번 비슷하게 나온 것), 여러 날 나온 키워드,
  오래 주목받은 특허, 특허가 몰린 기술 분류·출원인을 근거로 쓴다. 날짜를 쓸 때는 '11-03' 또는 '3일'처럼 쓴다.
- `watch`: 다음 달에 지켜볼 것 1~5개 (한 문장씩). 이번 달 이야기에서 이어질 만한 것.
- 물질 이름은 리튬인산철(LFP)로 쓴다.

`data/monthly-commentary.json`에 저장한다 (UTF-8).

```json
{"summary": "...", "trends": [{"title": "...", "body": "...", "topic": "이차전지"}], "watch": ["..."]}
```

```
.venv\Scripts\python.exe -m patent_radar monthly save data/monthly-commentary.json --page data/monthly-page.json
```

"형식 오류"가 나오면 메시지대로 고쳐 다시 저장한다. `--month`를 썼으면 여기에도 같은 `--month`를 붙인다.

## 3. claude.ai 페이지에 올리기

1. `ArtifactData`: `action: "get"`, 위 `url`, `collection: "monthly_reports"`, `doc_id`는 그달(YYYY-MM).
2. `action: "set"`, 같은 `url`·`collection`·`doc_id`, `file_path`는 `data/monthly-page.json`의 절대 경로.
   1에서 문서가 있었으면 그 `version`을 `if_version`으로 넘긴다.

실패해도 다시 시도하지 말고 보고에 적는다.

## 4. GitHub에 올리기

1. `.venv\Scripts\python.exe -m patent_radar site export`
2. Bash 도구로 `git add reports docs`, `git diff --cached --name-only`. `reports/`·`docs/` 밖의 파일이 있으면 멈추고 보고한다.
3. `git commit -m "월간 리포트 YYYY-MM"` (안내된 Co-Authored-By 줄이 있으면 붙인다).
4. `git pull --rebase --autostash origin main`. 실패하면 push하지 말고 오류를 보고한다.
   충돌로 멈췄으면 `git rebase --abort`로 되돌린 뒤 보고한다.
5. `git push origin main`. 실패하면 다시 시도하지 말고 오류를 보고한다.

## 5. 보고하기

한국어로 짧게: 한 달 흐름 요약, 흐름 제목들, 가장 오래 주목받은 특허 3건, 지난달과의 비교, 다음 달에 지켜볼 것,
공개 사이트의 "월간 리포트" 탭(https://chojiwon00.github.io/patent-radar/#monthly)과 GitHub(reports/monthly/)에서 볼 수 있다는 안내.
