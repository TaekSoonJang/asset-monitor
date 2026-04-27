# Asset Monitor

로그인된 증권사 웹 화면 또는 거래소 API에서 자산 정보를 수집하고, Google Sheets의 최신 자산/요약/실행 로그 탭에 반영하는 Python 프로젝트입니다.

브로커별 수집 로직은 `src/asset_monitor/brokers/<broker_name>/` 아래에 분리되어 있고, 공통 pipeline이 수집 결과를 `AssetRecord`로 모아 Google Sheets에 업데이트합니다.

## Supported Brokers

- `shinhan`: 신한투자증권
- `miraeasset`: 미래에셋증권
- `kiwoom`: 키움증권
- `upbit`: 업비트

## Project Structure

- `src/asset_monitor/pipeline.py`: 공통 실행 pipeline
- `src/asset_monitor/brokers/registry.py`: 브로커별 collector 선택
- `src/asset_monitor/brokers/shinhan/`: 신한투자증권 수집 로직
- `src/asset_monitor/brokers/miraeasset/`: 미래에셋증권 수집 로직
- `src/asset_monitor/brokers/kiwoom/`: 키움증권 수집 로직
- `src/asset_monitor/brokers/upbit/`: 업비트 API 수집 로직
- `src/asset_monitor/parsing.py`: 공통 파싱/요약
- `src/asset_monitor/sheets.py`: Google Sheets 업데이트
- `config/accounts.sample.json`: 계정 설정 예시
- `config/selectors.sample.json`: 화면 selector 설정 예시

## Windows Setup

이 머신에서는 `python` 명령이 Microsoft Store alias로 잡혀 짧게 `Python`만 출력하고 종료될 수 있습니다. 프로젝트 실행은 항상 가상환경의 Python을 직접 지정하는 방식을 권장합니다.

최초 1회:

```powershell
cd C:\Users\Zbook15G5\Documents\workspace\asset_monitor

# Python 3.11+ 설치 환경에서는 아래 명령 사용
py -3 -m venv .venv

# py 명령이 없다면, 사용 가능한 Python 3.11+ 경로로 venv 생성
# 예: & "C:\Path\To\python.exe" -m venv .venv

.\.venv\Scripts\python.exe -m pip install -e .[dev]
```

현재 작업공간에는 `.venv`가 이미 생성되어 있고 패키지도 설치되어 있습니다.

Windows에서 `Asia/Seoul` timezone을 읽기 위해 `tzdata`가 필요합니다. 이 프로젝트의 기본 의존성에 포함되어 있으므로 `pip install -e .[dev]`를 다시 실행하면 같이 설치됩니다.

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
UPBIT_API_BASE_URL=https://api.upbit.com
```

민감정보가 들어가는 `.env`, `config/accounts.json`, `service-account*.json`은 저장소에 커밋하지 않습니다.

## Account Configuration

`config/accounts.sample.json`을 복사해서 로컬 전용 `config/accounts.json`을 만듭니다.

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
      },
      "upbit": {
        "access_key": "your-upbit-access-key",
        "secret_key": "your-upbit-secret-key",
        "account_name": "Upbit",
        "min_amount_krw": 10000
      }
    }
  }
]
```

설정 설명:

- `name`: Google Sheets에 표시할 소유자 이름
- `profile_name`: 브라우저 프로필 구분용 이름
- `cdp_url`: 로그인된 Chrome DevTools Protocol 주소. 업비트만 사용하는 계정은 생략 가능
- `brokers`: 실행할 브로커 설정 묶음
- `upbit.access_key`: 업비트 Open API Access Key
- `upbit.secret_key`: 업비트 Open API Secret Key
- `upbit.account_name`: 시트에 표시할 업비트 계정 이름
- `upbit.min_amount_krw`: 이 금액 미만의 업비트 자산은 표시하지 않음

업비트 API Key는 `자산조회` 권한만 필요합니다. 허용 IP에는 실행 환경의 공인 IP를 등록해야 합니다.

현재 공인 IP 확인:

```powershell
Invoke-RestMethod -Uri "https://api.ipify.org?format=text"
```

## Run

항상 가상환경 Python으로 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m asset_monitor.cli
```

패키지 설치 후 console script도 사용할 수 있습니다.

```powershell
.\.venv\Scripts\asset-monitor.exe
```

`python -m asset_monitor.cli`처럼 bare `python` 명령을 쓰면 Windows Store alias 문제로 실패할 수 있으니 피합니다.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Hourly Run

작업 스케줄러에 등록할 때도 전체 경로를 사용합니다.

Program/script:

```text
C:\Users\Zbook15G5\Documents\workspace\asset_monitor\.venv\Scripts\python.exe
```

Arguments:

```text
-m asset_monitor.cli
```

Start in:

```text
C:\Users\Zbook15G5\Documents\workspace\asset_monitor
```

## Broker Notes

증권사 웹 화면은 selector, iframe 이름, AJAX 경로가 자주 바뀔 수 있습니다. 수집 실패 시 먼저 아래 항목을 확인합니다.

- 신한: `src/asset_monitor/brokers/shinhan/config.py`, `config/selectors.sample.json`
- 미래에셋: `src/asset_monitor/brokers/miraeasset/config.py`
- 키움: `src/asset_monitor/brokers/kiwoom/config.py`
- 업비트: API Key 권한, 허용 IP, `config/accounts.json`의 access/secret key
- 디버그 파일: `artifacts/debug/<broker>/.../*.html`
- 실행 로그: `logs/`

