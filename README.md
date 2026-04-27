# Asset Monitor

로그인된 증권사 웹 세션에 Chrome DevTools Protocol(CDP)로 연결해 자산 정보를 수집하고, Google Sheets에 최신 상태를 반영하는 Python 프로젝트입니다.

현재는 여러 금융기관을 같은 실행 파이프라인에서 처리할 수 있도록 `broker` 단위로 구성되어 있습니다.

## Supported Brokers

- `shinhan`: 신한투자증권
- `miraeasset`: 미래에셋증권
- `kiwoom`: 키움증권

## Project Structure

- `src/asset_monitor/pipeline.py`: 공통 실행 파이프라인
- `src/asset_monitor/brokers/registry.py`: 브로커별 collector 선택
- `src/asset_monitor/brokers/shinhan/`: 신한투자증권 전용 로직
- `src/asset_monitor/brokers/miraeasset/`: 미래에셋증권 전용 로직
- `src/asset_monitor/brokers/kiwoom/`: 키움증권 전용 로직
- `src/asset_monitor/parsing.py`: 공통 파싱 및 요약
- `src/asset_monitor/sheets.py`: Google Sheets 쓰기
- `config/accounts.sample.json`: 계정 설정 예시
- `config/selectors.sample.json`: 신한 화면 selector 예시

## Environment Variables

필수:

```env
GOOGLE_SPREADSHEET_ID=your-spreadsheet-id
ACCOUNTS_CONFIG_PATH=config/accounts.json
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
```

또는 `GOOGLE_SERVICE_ACCOUNT_FILE` 대신 서비스 계정 JSON 전체를 환경변수로 넣을 수 있습니다.

```env
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
```

선택:

```env
ASSET_TARGETS=domestic,foreign,cash
TIMEZONE=Asia/Seoul
SELECTOR_CONFIG_PATH=config/selectors.sample.json
DEBUG_OUTPUT_DIR=artifacts/debug
LOGS_DIR=logs
LOCK_FILE=.asset-monitor.lock

MIRAEASSET_ACCOUNT_ASSETS_URL=https://securities.miraeasset.com/hkd/hkd1002/r01.do?acno=
MIRAEASSET_PENSION_BALANCE_URL=https://securities.miraeasset.com/hkp/hkp1002/r01.do
MIRAEASSET_RETIREMENT_PENSION_BALANCE_URL=https://securities.miraeasset.com/hkp/hkp2001/r01.do
KIWOOM_DOMESTIC_URL=https://www1.kiwoom.com/h/mykiwoom/asset/VTotalBalanceDomesticView
KIWOOM_FOREIGN_URL=https://www1.kiwoom.com/h/mykiwoom/asset/VTotalBalanceForeignView
```

민감정보가 들어가는 `.env`, `config/accounts.json`, `service-account*.json`은 저장소에 커밋하지 않습니다.

## Account Configuration

`config/accounts.sample.json`을 복사해서 로컬 전용 `config/accounts.json`을 만들고 실제 값을 채웁니다.

```powershell
Copy-Item config/accounts.sample.json config/accounts.json
```

예시:

```json
[
  {
    "name": "owner-name",
    "profile_name": "main-browser",
    "cdp_url": "http://127.0.0.1:9222",
    "brokers": {
      "shinhan": {
        "domestic_account_number": "000-00-000000",
        "account_inquiry_password": "0000"
      },
      "miraeasset": {
        "account_number": "000-0000-0000-0",
        "pension_account_number": "000-00-0000000",
        "retirement_account_number": "000-0000-0000-0"
      },
      "kiwoom": {
        "account_number": "0000-0000",
        "account_inquiry_password": "000000"
      }
    }
  }
]
```

필드 설명:

- `name`: Google Sheets에 표시할 소유자 이름
- `profile_name`: 선택 항목이며, 여러 브라우저 프로필을 구분할 때 사용
- `cdp_url`: 로그인된 Chrome 디버깅 주소
- `brokers`: 실행할 브로커별 설정
- `brokers.shinhan.domestic_account_number`: 신한 국내주식 조회에 사용할 계좌번호
- `brokers.shinhan.account_inquiry_password`: 신한 조회 비밀번호
- `brokers.miraeasset.account_number`: 미래에셋 일반 자산 계좌번호
- `brokers.miraeasset.pension_account_number`: 미래에셋 개인연금 계좌번호
- `brokers.miraeasset.retirement_account_number`: 미래에셋 퇴직연금 계좌번호
- `brokers.kiwoom.account_number`: 키움 조회에 사용할 계좌번호
- `brokers.kiwoom.account_inquiry_password`: 키움 조회 비밀번호

`brokers` 아래에 여러 브로커를 넣으면 로더가 브로커별 실행 단위로 자동 분리합니다.

## Run

로그인된 Chrome을 원격 디버깅 포트로 실행한 뒤 `.env`와 `config/accounts.json`을 준비합니다.

```powershell
asset-monitor
```

또는:

```powershell
python -m asset_monitor.cli
```

## Test

```powershell
pytest
```

## Broker Notes

금융사 웹 화면은 selector, iframe 이름, AJAX 경로가 자주 바뀔 수 있습니다. 수집이 실패하면 먼저 아래 항목을 확인하세요.

- 신한: `src/asset_monitor/brokers/shinhan/config.py`의 URL과 `config/selectors.sample.json`의 selector
- 미래에셋: `src/asset_monitor/brokers/miraeasset/config.py`의 route, DOM id, AJAX 경로
- 키움: `src/asset_monitor/brokers/kiwoom/config.py`의 route, DOM id
- 디버그 파일: `artifacts/debug/<broker>/.../*.html`
- 실행 로그: `logs/`

새 브로커를 추가할 때는 `src/asset_monitor/brokers/<broker_name>/` 아래에 collector와 config를 만들고, `src/asset_monitor/brokers/registry.py`에 등록합니다.
