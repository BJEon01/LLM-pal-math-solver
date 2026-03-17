# llm-pal-math-solver

LLM과 PAL(Program-aided Language Models)을 이용해 자연어 수학 문제를 Python 코드로 변환하고, 이를 검증 및 실행해 답을 구하는 캡스톤 프로젝트입니다.

기본 아이디어는 LLM이 계산을 직접 끝내는 대신, 문제를 실행 가능한 코드로 바꾸고 Python이 실제 계산을 담당하게 하는 것입니다. 이를 통해 풀이 과정의 투명성을 높이고, 다단계 계산에서의 정확도를 개선하는 것을 목표로 합니다.

## 프로젝트 개요

이 프로젝트는 다음 흐름으로 동작합니다.

1. 사용자가 자연어 수학 문제를 입력합니다.
2. LLM이 문제를 Python 코드 형태로 변환합니다.
3. 생성된 코드를 문법, 보안, 의미 측면에서 검증합니다.
4. 검증을 통과한 코드를 제한된 환경에서 실행합니다.
5. 최종 답과 함께 중간 산출물을 사용자에게 보여줍니다.

핵심 관심사는 아래 두 가지입니다.

- 자연어 문제를 안정적으로 코드로 바꾸는 프롬프트 설계
- 생성된 코드를 안전하게 실행하기 위한 검증 파이프라인

## 저장소 구성

- `app.py`
  Streamlit 기반 데모 UI입니다. 현재는 프로젝트 개념을 보여주는 프로토타입 형태입니다.
- `scripts/evaluate_gsm8k_pal.py`
  GSM8K 데이터셋에서 `Direct prompting`과 `PAL` 방식을 비교 평가하는 스크립트입니다.
- `prompts/prompt_en.txt`
  PAL 코드 생성을 위한 기본 프롬프트 파일입니다.
- `data/grade-school-math/`
  평가에 사용하는 GSM8K 관련 데이터와 예제 코드가 포함되어 있습니다.
- `docs/validation_design.md`
  코드 검증 설계 아이디어를 정리한 문서입니다.
- `local_only/`
  발표자료, 실험 결과, 개인 메모, 임시 파일 등을 보관하는 로컬 전용 폴더입니다. `.gitignore`로 제외됩니다.

## 코드 검증 단계

PAL 파이프라인은 생성된 코드를 바로 실행하지 않고 다음 단계로 검사합니다.

1. `Syntax`
   Python 문법이 올바른지 확인합니다.
2. `Security`
   `import`, 파일 접근, 네트워크 접근, 동적 실행 같은 위험한 구문을 차단합니다.
3. `Semantic`
   정의되지 않은 변수, 부적절한 연산, `answer` 변수 누락 등을 검사합니다.
4. `Runtime timeout`
   subprocess와 timeout으로 과도한 실행이나 비정상 실행을 제한합니다.

이 구조는 "코드 생성"과 "코드 실행" 사이에 안전 장치를 두기 위한 목적입니다.

## 실행 방법

필요한 패키지 설치:

```bash
pip install streamlit tqdm
```

데모 실행:

```bash
streamlit run app.py
```

평가 실행 예시:

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your_api_key"
python scripts/evaluate_gsm8k_pal.py --limit 200 --model gpt-5-mini --output gsm8k_eval_results.json
```

평가 스크립트 기본 경로:

- dataset: `data/grade-school-math/grade_school_math/data/test.jsonl`
- prompt: `prompts/prompt_en.txt`

## 현재 상태

- `app.py`는 아직 실제 서비스 수준의 완성형 웹앱은 아니며, 데모 성격이 강합니다.
- 평가 스크립트는 GSM8K 기준으로 Direct vs PAL 비교 실험을 수행할 수 있습니다.
- 공개 저장소 업로드를 위해 코드와 로컬 전용 산출물을 분리해 둔 상태입니다.

## 주의사항

- API 키는 코드에 직접 넣지 말고 환경 변수로 관리하는 것이 좋습니다.
- `local_only/` 아래 파일은 개인 작업 기록용이므로 공개 커밋 대상이 아닙니다.
