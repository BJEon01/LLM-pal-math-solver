import html

import streamlit as st

st.set_page_config(page_title="수학 문제 풀이 웹서비스", page_icon="🧮", layout="wide")

st.markdown(
    """
    <style>
    html, body, [data-testid="stAppViewContainer"], .stApp {
        overflow-y: scroll !important;
    }
    .stApp {
        background: linear-gradient(180deg, #e9edf1 0%, #dde3e9 100%);
        color: #1f2937;
    }
    .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
    }
    [data-testid="stHeader"] {
        background: rgba(233, 237, 241, 0.94);
    }
    [data-testid="stSidebar"] {
        background: #e3e8ee;
    }
    .stApp h1,
    .stApp h2,
    .stApp h3,
    .stApp label,
    .stApp p,
    .stApp .stMarkdown,
    .stApp .stCaption {
        color: #1f2937;
    }
    .tight-caption {
        margin-bottom: 0.15rem;
    }
    .tight-divider {
        margin-top: 0.2rem;
        margin-bottom: 0.45rem;
        border: none;
        border-top: 1px solid #c5d0db;
    }
    .final-answer-card {
        background: #f8f3ea;
        border: 2px solid #c89b5d;
        border-radius: 16px;
        padding: 1rem 1.1rem;
        box-shadow: 0 8px 24px rgba(130, 97, 52, 0.08);
        margin-bottom: 0.5rem;
    }
    .final-answer-label {
        font-size: 0.82rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        color: #8a5a22;
        margin-bottom: 0.35rem;
        text-transform: uppercase;
    }
    .final-answer-text {
        font-size: 1.1rem;
        font-weight: 600;
        color: #2b2f33;
        line-height: 1.5;
    }
    [data-baseweb="select"] > div,
    [data-baseweb="input"] > div,
    [data-baseweb="textarea"] > div {
        background-color: #eef2f6;
        color: #1f2937;
        border: 1px solid #c5d0db;
        border-radius: 12px;
    }
    [data-baseweb="select"] span,
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea {
        color: #1f2937 !important;
    }
    [data-baseweb="textarea"] textarea::placeholder {
        color: #6b7280 !important;
    }
    .stButton > button {
        background: #d0d9e4;
        color: #163a5f;
        border: 1px solid #aebccb;
        border-radius: 999px;
    }
    .stButton > button:hover {
        background: #c4d1de;
        color: #102c47;
        border-color: #9eb1c3;
    }
    div[data-testid="stCodeBlock"] {
        background: #edf1f5;
        border: 1px solid #c5d0db;
        border-radius: 14px;
    }
    div[data-testid="stAlert"] {
        border-radius: 14px;
    }
    div[data-testid="stAlert"] * {
        color: #1f2937 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧮 자연어 기반 수학 문제 풀이 웹서비스")
st.write("자연어 문제를 입력하면 Python 코드, 실행 결과, 최종 답을 보여주는 프로토타입입니다.")

# 예시 문제별 mock 데이터
example_db = {
    "사과 3개의 가격이 6000원일 때, 사과 1개의 가격은 얼마인가요?": {
        "code": "total_price = 6000\nnum_apples = 3\nanswer = total_price / num_apples\nprint(answer)",
        "execution": "2000.0",
        "final_answer": "사과 1개의 가격은 2000원입니다.",
        "validation": "통과"
    },
    "민수는 12000원을 가지고 있고, 3000원짜리 음료를 몇 개 살 수 있나요?": {
        "code": "money = 12000\nprice = 3000\nanswer = money // price\nprint(answer)",
        "execution": "4",
        "final_answer": "민수는 음료를 4개 살 수 있습니다.",
        "validation": "통과"
    },
    "한 반에 학생이 24명 있고, 6명씩 조를 만들면 몇 조가 되나요?": {
        "code": "students = 24\ngroup_size = 6\nanswer = students // group_size\nprint(answer)",
        "execution": "4",
        "final_answer": "총 4조가 됩니다.",
        "validation": "통과"
    }
}

st.subheader("문제 입력")

selected_example = st.selectbox(
    "예시 문제를 선택하거나 아래에 직접 입력하세요.",
    ["직접 입력"] + list(example_db.keys())
)

if selected_example == "직접 입력":
    user_input = st.text_area(
        "자연어 수학 문제를 입력하세요.",
        placeholder="예: 사과 3개의 가격이 6000원일 때, 사과 1개의 가격은 얼마인가요?",
        height=120
    )
else:
    user_input = st.text_area(
        "자연어 수학 문제를 입력하세요.",
        value=selected_example,
        height=120
    )

run_button = st.button("문제 풀이 실행")

def get_mock_result(question: str):
    question = question.strip()
    if question in example_db:
        return example_db[question]
    return {
        "code": "# 아직 해당 문제에 대한 모델 연동이 되지 않았습니다.\n# 추후 LLM이 여기에 Python 코드를 생성합니다.",
        "execution": "실행 대기",
        "final_answer": "현재는 프로토타입 단계로, 예시 문제에 대해서만 결과를 제공합니다.",
        "validation": "대기"
    }

if run_button:
    if not user_input.strip():
        st.warning("문제를 입력해주세요.")
    else:
        result = get_mock_result(user_input)

        st.subheader("최종 답변")
        st.markdown(
            f'''
            <div class="final-answer-card">
                <div class="final-answer-label">Answer</div>
                <div class="final-answer-text">{html.escape(result["final_answer"])}</div>
            </div>
            ''',
            unsafe_allow_html=True,
        )

        st.markdown(
            '<p class="tight-caption">아래 내용은 참고용 상세 정보입니다.</p>',
            unsafe_allow_html=True,
        )
        st.markdown('<hr class="tight-divider">', unsafe_allow_html=True)
        st.subheader("참고 정보")

        st.markdown("**1. 생성된 Python 코드**")
        st.code(result["code"], language="python")

        st.markdown("**2. 코드 검증 결과**")
        if result["validation"] == "통과":
            st.success("검증 결과: 통과")
        elif result["validation"] == "대기":
            st.info("검증 결과: 대기")
        else:
            st.error(f"검증 결과: {result['validation']}")

        st.markdown("**3. 실행 결과**")
        st.code(str(result["execution"]))

        st.markdown("---")
        st.subheader("시스템 처리 흐름")
        st.write("입력 문제 → Python 코드 생성 → 코드 검증 → 실행 결과 확인 → 최종 답변 제공")
