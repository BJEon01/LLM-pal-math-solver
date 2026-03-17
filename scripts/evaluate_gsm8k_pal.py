import argparse
import ast
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATASET_PATH = os.path.join(
    PROJECT_ROOT,
    "data",
    "grade-school-math",
    "grade_school_math",
    "data",
    "test.jsonl",
)
DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_PAL_PROMPT_FILE = os.path.join(PROJECT_ROOT, "prompts", "prompt_en.txt")
# Keep this empty in shared/public repos. Prefer OPENAI_API_KEY instead.
API_KEY_IN_SCRIPT = ""
MAX_CODE_CHARS = 4000
MAX_AST_NODES = 256
MAX_INTEGER_DIGITS = 12
MAX_POWER_EXPONENT = 8
MAX_STDOUT_CHARS = 4000
DEFAULT_PAL_TIMEOUT_SECONDS = 2.0
NUMBER_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
GOLD_RE = re.compile(r"####\s*([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)")
CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

DIRECT_INSTRUCTIONS_DEFAULT = None
DIRECT_NUMERIC_ONLY_INSTRUCTIONS = (
    "Solve the math problem and return only the final numeric answer.\n"
    "Do not include explanation, units, commas, or any extra text."
)

PAL_INSTRUCTIONS_DEFAULT = (
    "You convert grade-school math word problems into executable Python.\n"
    "Rules:\n"
    "1) Output only Python code (no markdown fences).\n"
    "2) Do not import anything.\n"
    "3) Define intermediate variables as needed.\n"
    "4) Store the final numeric result in a variable named answer.\n"
    "5) Do not print explanation text."
)

ALLOWED_AST_NODES = (
    ast.Module,
    ast.Assign,
    ast.AugAssign,
    ast.If,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Call,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.IfExp,
)

SAFE_BUILTINS = {
    "abs": abs,
    "int": int,
    "float": float,
    "round": round,
    "min": min,
    "max": max,
}

BLOCKED_IDENTIFIERS = {
    "os",
    "sys",
    "open",
    "read",
    "write",
    "shutil",
    "eval",
    "exec",
    "__import__",
    "socket",
    "requests",
    "urllib",
    "subprocess",
}

RUNTIME_WRAPPER = """
import json
import sys

SAFE_BUILTINS = {
    "abs": abs,
    "int": int,
    "float": float,
    "round": round,
    "min": min,
    "max": max,
}

code = sys.stdin.read()
locals_dict = {}

try:
    exec(compile(code, "<pal_code>", "exec"), {"__builtins__": SAFE_BUILTINS}, locals_dict)
    if "answer" not in locals_dict:
        payload = {"ok": False, "error": "missing `answer` variable"}
    else:
        value = locals_dict["answer"]
        if isinstance(value, bool):
            payload = {"ok": False, "error": "answer is boolean"}
        elif isinstance(value, (int, float, str)):
            payload = {"ok": True, "answer": str(value)}
        else:
            payload = {"ok": False, "error": f"unsupported answer type: {type(value).__name__}"}
except Exception as exc:
    payload = {"ok": False, "error": f"execution error: {exc}"}

sys.stdout.write(json.dumps(payload))
"""


@dataclass
class EvalResult:
    idx: int
    question: str
    gold_answer: str
    gold_number: Optional[str]
    direct_raw: str
    direct_pred: Optional[str]
    direct_correct: bool
    pal_code: str
    pal_error_stage: Optional[str]
    pal_exec_error: Optional[str]
    pal_pred: Optional[str]
    pal_correct: bool


@dataclass
class PalExecutionResult:
    pred: Optional[str]
    error_stage: Optional[str]
    error_detail: Optional[str]


def normalize_number_str(text: str) -> Optional[str]:
    if text is None:
        return None
    text = text.strip().replace(",", "")
    if not text:
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    if d == d.to_integral_value():
        return format(d.quantize(Decimal(1)), "f")
    plain = format(d.normalize(), "f")
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    return plain


def extract_gold_number(answer_text: str) -> Optional[str]:
    match = GOLD_RE.search(answer_text)
    if not match:
        return None
    return normalize_number_str(match.group(1))


def extract_pred_number(text: str) -> Optional[str]:
    if text is None:
        return None
    candidates = NUMBER_RE.findall(text)
    if not candidates:
        return None
    return normalize_number_str(candidates[-1])


def extract_python_code(text: str) -> str:
    if not text:
        return ""
    match = CODE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def parse_code_to_ast(code: str) -> tuple[Optional[ast.Module], Optional[str]]:
    try:
        return ast.parse(code, mode="exec"), None
    except SyntaxError as exc:
        return None, f"invalid Python syntax: {exc}"


def validate_syntax(code: str) -> tuple[Optional[ast.Module], Optional[str]]:
    if not code:
        return None, "empty code"
    if len(code) > MAX_CODE_CHARS:
        return None, f"code too long: {len(code)} chars"

    tree, parse_error = parse_code_to_ast(code)
    if parse_error:
        return None, parse_error
    if tree is None:
        return None, "failed to parse code"
    if len(list(ast.walk(tree))) > MAX_AST_NODES:
        return None, f"AST too large: more than {MAX_AST_NODES} nodes"

    return tree, None


def validate_security(tree: ast.Module) -> Optional[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST_NODES):
            return f"disallowed AST node: {type(node).__name__}"

        if isinstance(node, ast.Name) and node.id in BLOCKED_IDENTIFIERS:
            return f"blocked identifier: {node.id}"

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return "disallowed function call target"
            if node.func.id not in SAFE_BUILTINS:
                return f"disallowed function: {node.func.id}"
            if node.keywords:
                return "keyword arguments are not allowed"

    return None


def validate_numeric_constant(node: ast.Constant) -> Optional[str]:
    if isinstance(node.value, bool):
        return "boolean literals are not allowed"
    if not isinstance(node.value, (int, float)):
        return f"non-numeric constant is not allowed: {type(node.value).__name__}"
    if isinstance(node.value, int) and len(str(abs(node.value))) > MAX_INTEGER_DIGITS:
        return f"integer literal too large: {node.value}"
    return None


def infer_known_small_int(node: ast.AST, known_ints: dict[str, int]) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = infer_known_small_int(node.operand, known_ints)
        if value is None:
            return None
        return value if isinstance(node.op, ast.UAdd) else -value

    if isinstance(node, ast.Name):
        return known_ints.get(node.id)

    return None


def infer_numeric_expr_type(
    node: ast.AST,
    symbols: dict[str, str],
    known_ints: dict[str, int],
) -> tuple[Optional[str], Optional[str]]:
    if isinstance(node, ast.Constant):
        constant_error = validate_numeric_constant(node)
        if constant_error:
            return None, constant_error
        return "number", None

    if isinstance(node, ast.Name):
        if node.id not in symbols:
            return None, f"undefined variable: {node.id}"
        return symbols[node.id], None

    if isinstance(node, ast.UnaryOp):
        operand_type, operand_error = infer_numeric_expr_type(node.operand, symbols, known_ints)
        if operand_error:
            return None, operand_error
        if operand_type != "number":
            return None, "unary operators require numeric operands"
        return "number", None

    if isinstance(node, ast.BinOp):
        left_type, left_error = infer_numeric_expr_type(node.left, symbols, known_ints)
        if left_error:
            return None, left_error
        right_type, right_error = infer_numeric_expr_type(node.right, symbols, known_ints)
        if right_error:
            return None, right_error
        if left_type != "number" or right_type != "number":
            return None, "binary operators require numeric operands"

        if isinstance(node.op, ast.Pow):
            exponent_value = infer_known_small_int(node.right, known_ints)
            if exponent_value is None:
                return None, "power operator requires a small integer exponent"
            if abs(exponent_value) > MAX_POWER_EXPONENT:
                return None, f"power exponent too large: {exponent_value}"

        return "number", None

    if isinstance(node, ast.Compare):
        left_type, left_error = infer_numeric_expr_type(node.left, symbols, known_ints)
        if left_error:
            return None, left_error
        if left_type != "number":
            return None, "comparisons require numeric operands"

        for comparator in node.comparators:
            right_type, right_error = infer_numeric_expr_type(comparator, symbols, known_ints)
            if right_error:
                return None, right_error
            if right_type != "number":
                return None, "comparisons require numeric operands"

        return "bool", None

    if isinstance(node, ast.BoolOp):
        for value in node.values:
            value_type, value_error = infer_numeric_expr_type(value, symbols, known_ints)
            if value_error:
                return None, value_error
            if value_type != "bool":
                return None, "boolean operators require boolean operands"
        return "bool", None

    if isinstance(node, ast.IfExp):
        test_error = infer_condition_type(node.test, symbols, known_ints)
        if test_error:
            return None, test_error

        body_type, body_error = infer_numeric_expr_type(node.body, symbols, known_ints)
        if body_error:
            return None, body_error
        else_type, else_error = infer_numeric_expr_type(node.orelse, symbols, known_ints)
        if else_error:
            return None, else_error
        if body_type != else_type:
            return None, "ternary expression branches must have the same type"
        return body_type, None

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            return None, "function target must be a simple name"
        func_name = node.func.id
        if func_name not in SAFE_BUILTINS:
            return None, f"disallowed function: {func_name}"

        for arg in node.args:
            arg_type, arg_error = infer_numeric_expr_type(arg, symbols, known_ints)
            if arg_error:
                return None, arg_error
            if arg_type != "number":
                return None, f"{func_name} requires numeric arguments"

        if func_name in {"abs", "int", "float"} and len(node.args) != 1:
            return None, f"{func_name} expects exactly one argument"
        if func_name == "round" and len(node.args) not in {1, 2}:
            return None, "round expects one or two arguments"
        if func_name in {"min", "max"} and len(node.args) < 1:
            return None, f"{func_name} expects at least one argument"

        return "number", None

    return None, f"unsupported expression node: {type(node).__name__}"


def infer_condition_type(
    node: ast.AST,
    symbols: dict[str, str],
    known_ints: dict[str, int],
) -> Optional[str]:
    expr_type, expr_error = infer_numeric_expr_type(node, symbols, known_ints)
    if expr_error:
        return expr_error
    if expr_type != "bool":
        return "if conditions must evaluate to a boolean value"
    return None


def validate_statements(
    statements: list[ast.stmt],
    symbols: dict[str, str],
    known_ints: dict[str, int],
) -> Optional[str]:
    for stmt in statements:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                return "assignments must target a single variable name"
            target_name = stmt.targets[0].id
            if target_name in BLOCKED_IDENTIFIERS:
                return f"blocked identifier: {target_name}"
            value_type, value_error = infer_numeric_expr_type(stmt.value, symbols, known_ints)
            if value_error:
                return value_error
            symbols[target_name] = value_type
            known_value = infer_known_small_int(stmt.value, known_ints)
            if known_value is None:
                known_ints.pop(target_name, None)
            else:
                known_ints[target_name] = known_value
            continue

        if isinstance(stmt, ast.AugAssign):
            if not isinstance(stmt.target, ast.Name):
                return "augmented assignment must target a variable name"
            target_name = stmt.target.id
            if target_name not in symbols:
                return f"undefined variable in augmented assignment: {target_name}"
            value_type, value_error = infer_numeric_expr_type(stmt.value, symbols, known_ints)
            if value_error:
                return value_error
            if symbols[target_name] != "number" or value_type != "number":
                return "augmented assignment requires numeric operands"
            known_ints.pop(target_name, None)
            continue

        if isinstance(stmt, ast.If):
            condition_error = infer_condition_type(stmt.test, symbols, known_ints)
            if condition_error:
                return condition_error

            body_symbols = symbols.copy()
            body_known_ints = known_ints.copy()
            body_error = validate_statements(stmt.body, body_symbols, body_known_ints)
            if body_error:
                return body_error

            else_symbols = symbols.copy()
            else_known_ints = known_ints.copy()
            if stmt.orelse:
                else_error = validate_statements(stmt.orelse, else_symbols, else_known_ints)
                if else_error:
                    return else_error

            original_names = set(symbols)
            for name in original_names:
                if name in body_symbols and body_symbols[name] != symbols[name]:
                    return f"type changed inside if block: {name}"
                if stmt.orelse and name in else_symbols and else_symbols[name] != symbols[name]:
                    return f"type changed inside if block: {name}"

            if stmt.orelse:
                common_names = set(body_symbols) & set(else_symbols)
                for name in common_names:
                    if body_symbols[name] != else_symbols[name]:
                        return f"type mismatch across if branches: {name}"
                    if name not in symbols:
                        symbols[name] = body_symbols[name]
                        if name in body_known_ints and name in else_known_ints:
                            if body_known_ints[name] == else_known_ints[name]:
                                known_ints[name] = body_known_ints[name]
            continue

        return f"unsupported statement for arithmetic profile: {type(stmt).__name__}"

    return None


def validate_semantics(tree: ast.Module) -> Optional[str]:
    symbols: dict[str, str] = {}
    known_ints: dict[str, int] = {}

    validation_error = validate_statements(tree.body, symbols, known_ints)
    if validation_error:
        return validation_error

    if "answer" not in symbols:
        return "missing `answer` assignment"
    if symbols["answer"] != "number":
        return "`answer` must be numeric"

    return None


def execute_runtime_sandbox(code: str, timeout_seconds: float) -> tuple[Optional[str], Optional[str]]:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", RUNTIME_WRAPPER],
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"execution timed out after {timeout_seconds:.1f}s"
    except OSError as exc:
        return None, f"failed to start subprocess: {exc}"

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        return None, f"subprocess failed with code {completed.returncode}: {stderr or 'no stderr'}"

    if len(completed.stdout) > MAX_STDOUT_CHARS:
        return None, f"stdout too large: {len(completed.stdout)} chars"

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return None, f"invalid runtime payload: {exc}"

    if payload.get("ok"):
        return payload.get("answer"), None
    return None, payload.get("error", "unknown runtime error")


def execute_pal_code(code: str, timeout_seconds: float) -> PalExecutionResult:
    code = extract_python_code(code)
    if not code:
        return PalExecutionResult(None, "syntax", "empty code")

    tree, syntax_error = validate_syntax(code)
    if syntax_error:
        return PalExecutionResult(None, "syntax", syntax_error)
    if tree is None:
        return PalExecutionResult(None, "syntax", "failed to parse code")

    security_error = validate_security(tree)
    if security_error:
        return PalExecutionResult(None, "security", security_error)

    semantic_error = validate_semantics(tree)
    if semantic_error:
        return PalExecutionResult(None, "semantic", semantic_error)

    runtime_pred, runtime_error = execute_runtime_sandbox(code, timeout_seconds)
    if runtime_error:
        return PalExecutionResult(None, "runtime", runtime_error)

    parsed = normalize_number_str(runtime_pred)
    if parsed is None:
        return PalExecutionResult(None, "runtime", "runtime returned a non-numeric answer")

    return PalExecutionResult(parsed, None, None)


def fetch_response_text(
    *,
    api_key: str,
    model: str,
    instructions: Optional[str],
    user_input: str,
    max_output_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    max_retries: int = 5,
    retry_base_sleep: float = 1.0,
) -> str:
    url = "https://api.openai.com/v1/responses"
    payload = {"model": model, "input": user_input}
    if instructions:
        payload["instructions"] = instructions
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw)
                text = data.get("output_text")
                if text:
                    return text

                outputs = data.get("output", [])
                chunks: list[str] = []
                for item in outputs:
                    for content in item.get("content", []):
                        if content.get("type") == "output_text":
                            chunks.append(content.get("text", ""))
                combined = "\n".join(chunks).strip()
                if combined:
                    return combined
                raise RuntimeError("No text found in API response.")
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in (408, 409, 429, 500, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(retry_base_sleep * (2**attempt))
                continue
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {status}: {error_body}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_retries - 1:
                time.sleep(retry_base_sleep * (2**attempt))
                continue
            raise RuntimeError(f"Network error: {exc}") from exc

    raise RuntimeError("Failed after retries.")


def load_gsm8k(dataset_path: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            rows.append(item)
    return rows


def load_previous_results(results_path: str) -> list[dict[str, Any]]:
    with open(results_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"Invalid results file: missing `results` list in {results_path}")
    return results


def select_retry_indices(previous_results: list[dict[str, Any]], retry_filter: str) -> list[int]:
    selected: list[int] = []
    seen: set[int] = set()

    for row in previous_results:
        idx = row.get("idx")
        if not isinstance(idx, int):
            continue

        direct_correct = bool(row.get("direct_correct"))
        pal_correct = bool(row.get("pal_correct"))

        should_include = False
        if retry_filter == "pal_wrong":
            should_include = not pal_correct
        elif retry_filter == "direct_wrong":
            should_include = not direct_correct
        elif retry_filter == "either_wrong":
            should_include = (not direct_correct) or (not pal_correct)
        elif retry_filter == "both_wrong":
            should_include = (not direct_correct) and (not pal_correct)
        else:
            raise RuntimeError(f"Unknown retry filter: {retry_filter}")

        if should_include and idx not in seen:
            selected.append(idx)
            seen.add(idx)

    return selected


def is_correct(pred: Optional[str], gold: Optional[str]) -> bool:
    if pred is None or gold is None:
        return False
    try:
        return Decimal(pred) == Decimal(gold)
    except InvalidOperation:
        return False


def estimate_runtime_seconds(
    *,
    samples: int,
    sleep_between_calls: float,
    assumed_call_seconds: float,
) -> float:
    # Per sample: 2 API calls (direct + PAL), and optional sleep after each call.
    per_sample = (2 * assumed_call_seconds) + (2 * max(0.0, sleep_between_calls))
    return samples * per_sample


def evaluate(
    *,
    api_key: str,
    model: str,
    dataset: list[dict[str, str]],
    indices: Optional[list[int]],
    limit: Optional[int],
    seed: int,
    sleep_between_calls: float,
    pal_timeout_seconds: float,
    direct_instructions: Optional[str],
    pal_instructions: str,
    direct_max_output_tokens: Optional[int],
    pal_max_output_tokens: Optional[int],
    reasoning_effort: Optional[str],
) -> tuple[list[EvalResult], dict[str, Any]]:
    if indices is None:
        indices = list(range(len(dataset)))
    if limit is not None and limit < len(indices):
        rng = random.Random(seed)
        indices = rng.sample(indices, k=limit)

    results: list[EvalResult] = []
    direct_correct_count = 0
    pal_correct_count = 0
    direct_parse_success = 0
    pal_exec_success = 0
    pal_error_stage_counts: Counter[str] = Counter()

    iterator = indices
    if tqdm is not None:
        iterator = tqdm(indices, desc="GSM8K eval", unit="sample")

    for i, idx in enumerate(iterator, start=1):
        row = dataset[idx]
        question = row["question"]
        gold_answer = row["answer"]
        gold_number = extract_gold_number(gold_answer)

        direct_raw = fetch_response_text(
            api_key=api_key,
            model=model,
            instructions=direct_instructions,
            user_input=question,
            max_output_tokens=direct_max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        direct_pred = extract_pred_number(direct_raw)
        if direct_pred is not None:
            direct_parse_success += 1
        direct_correct = is_correct(direct_pred, gold_number)
        if direct_correct:
            direct_correct_count += 1

        if sleep_between_calls > 0:
            time.sleep(sleep_between_calls)

        pal_raw_code = fetch_response_text(
            api_key=api_key,
            model=model,
            instructions=pal_instructions,
            user_input=question,
            max_output_tokens=pal_max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        pal_result = execute_pal_code(pal_raw_code, timeout_seconds=pal_timeout_seconds)
        pal_pred = pal_result.pred
        pal_exec_error = pal_result.error_detail
        if pal_result.error_stage is None and pal_pred is not None:
            pal_exec_success += 1
        if pal_result.error_stage is not None:
            pal_error_stage_counts[pal_result.error_stage] += 1
        pal_correct = is_correct(pal_pred, gold_number)
        if pal_correct:
            pal_correct_count += 1

        if sleep_between_calls > 0:
            time.sleep(sleep_between_calls)

        result = EvalResult(
            idx=idx,
            question=question,
            gold_answer=gold_answer,
            gold_number=gold_number,
            direct_raw=direct_raw,
            direct_pred=direct_pred,
            direct_correct=direct_correct,
            pal_code=pal_raw_code,
            pal_error_stage=pal_result.error_stage,
            pal_exec_error=pal_exec_error,
            pal_pred=pal_pred,
            pal_correct=pal_correct,
        )
        results.append(result)

        if tqdm is not None:
            iterator.set_postfix(
                direct_acc=f"{direct_correct_count / i:.3f}",
                pal_acc=f"{pal_correct_count / i:.3f}",
                refresh=False,
            )
        else:
            print(
                f"[{i}/{len(indices)}] "
                f"direct={int(direct_correct)} "
                f"pal={int(pal_correct)}"
            )

    total = len(results)
    summary = {
        "model": model,
        "samples": total,
        "direct_accuracy": (direct_correct_count / total) if total else 0.0,
        "pal_accuracy": (pal_correct_count / total) if total else 0.0,
        "direct_correct": direct_correct_count,
        "pal_correct": pal_correct_count,
        "direct_parse_success": direct_parse_success,
        "pal_exec_success": pal_exec_success,
        "pal_error_stage_counts": dict(sorted(pal_error_stage_counts.items())),
        "pal_timeout_seconds": pal_timeout_seconds,
        "direct_max_output_tokens": direct_max_output_tokens,
        "pal_max_output_tokens": pal_max_output_tokens,
        "reasoning_effort": reasoning_effort,
    }
    return results, summary


def maybe_load_pal_prompt(path: Optional[str]) -> str:
    prompt_path = path or DEFAULT_PAL_PROMPT_FILE
    if not os.path.exists(prompt_path):
        return PAL_INSTRUCTIONS_DEFAULT
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read().strip()
    return prompt if prompt else PAL_INSTRUCTIONS_DEFAULT


def maybe_load_direct_prompt(path: Optional[str]) -> Optional[str]:
    if not path:
        return DIRECT_INSTRUCTIONS_DEFAULT
    with open(path, "r", encoding="utf-8") as f:
        prompt = f.read().strip()
    return prompt if prompt else DIRECT_INSTRUCTIONS_DEFAULT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare GSM8K accuracy for direct prompting vs PAL on gpt-5-mini."
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="Path to GSM8K test.jsonl")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model name")
    parser.add_argument("--limit", type=int, default=100, help="Number of samples to evaluate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (for future use)")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between API calls")
    parser.add_argument(
        "--retry-from-results",
        default=None,
        help="Optional previous results JSON. If set, rerun only the filtered subset from that file.",
    )
    parser.add_argument(
        "--retry-filter",
        default="pal_wrong",
        choices=["pal_wrong", "direct_wrong", "either_wrong", "both_wrong"],
        help="Subset filter to use with --retry-from-results.",
    )
    parser.add_argument(
        "--direct-answer-only",
        action="store_true",
        help="Use a short direct baseline instruction that asks for the final numeric answer only.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        choices=["minimal", "low", "medium", "high", "xhigh"],
        help="Optional Responses API reasoning effort setting. Lower values are usually faster.",
    )
    parser.add_argument(
        "--direct-max-output-tokens",
        type=int,
        default=None,
        help="Optional max_output_tokens for the direct baseline.",
    )
    parser.add_argument(
        "--pal-max-output-tokens",
        type=int,
        default=None,
        help="Optional max_output_tokens for PAL code generation.",
    )
    parser.add_argument(
        "--pal-timeout-seconds",
        type=float,
        default=DEFAULT_PAL_TIMEOUT_SECONDS,
        help="Timeout for executing PAL-generated code in an isolated subprocess.",
    )
    parser.add_argument(
        "--assumed-call-seconds",
        type=float,
        default=3.0,
        help="Assumed average seconds per API call, used for runtime estimate print.",
    )
    parser.add_argument(
        "--pal-prompt-file",
        default=None,
        help="Optional prompt file for PAL code-generation instructions. Defaults to prompt.txt if present.",
    )
    parser.add_argument(
        "--direct-prompt-file",
        default=None,
        help="Optional prompt file for direct baseline instructions (default: question only).",
    )
    parser.add_argument(
        "--output",
        default="gsm8k_eval_results.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load data and print config only (no API calls).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = load_gsm8k(args.dataset)

    if args.limit is not None and args.limit > len(dataset):
        args.limit = len(dataset)

    retry_indices: Optional[list[int]] = None
    if args.retry_from_results:
        previous_results = load_previous_results(args.retry_from_results)
        retry_indices = select_retry_indices(previous_results, args.retry_filter)
        if not retry_indices:
            raise RuntimeError(
                f"No questions matched retry filter `{args.retry_filter}` in {args.retry_from_results}."
            )
        if args.limit is not None and args.limit < len(retry_indices):
            retry_indices = retry_indices[: args.limit]
        args.limit = len(retry_indices)

    pal_instructions = maybe_load_pal_prompt(args.pal_prompt_file)
    direct_instructions = maybe_load_direct_prompt(args.direct_prompt_file)
    if args.direct_answer_only:
        direct_instructions = DIRECT_NUMERIC_ONLY_INSTRUCTIONS
    planned_samples = len(retry_indices) if retry_indices is not None else (
        args.limit if args.limit is not None else len(dataset)
    )
    estimated_seconds = estimate_runtime_seconds(
        samples=planned_samples,
        sleep_between_calls=args.sleep,
        assumed_call_seconds=args.assumed_call_seconds,
    )
    estimated_minutes = estimated_seconds / 60.0

    print(f"Planned samples: {planned_samples}")
    print(f"Estimated runtime: about {estimated_minutes:.1f} minutes")

    if args.dry_run:
        print(f"Loaded dataset rows: {len(dataset)}")
        print(f"Model: {args.model}")
        print(f"Limit: {args.limit}")
        print(f"Retry from results: {args.retry_from_results}")
        print(f"Retry filter: {args.retry_filter}")
        print(f"Direct answer only: {args.direct_answer_only}")
        print(f"Reasoning effort: {args.reasoning_effort}")
        print(f"Direct max output tokens: {args.direct_max_output_tokens}")
        print(f"PAL max output tokens: {args.pal_max_output_tokens}")
        print(f"PAL timeout seconds: {args.pal_timeout_seconds}")
        print(f"Assumed call seconds: {args.assumed_call_seconds}")
        print(f"Output: {args.output}")
        if tqdm is None:
            print("tqdm not installed: fallback to plain progress logs.")
        return

    api_key = API_KEY_IN_SCRIPT.strip() or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set API_KEY_IN_SCRIPT in this file, or set OPENAI_API_KEY.")

    results, summary = evaluate(
        api_key=api_key,
        model=args.model,
        dataset=dataset,
        indices=retry_indices,
        limit=args.limit,
        seed=args.seed,
        sleep_between_calls=args.sleep,
        pal_timeout_seconds=args.pal_timeout_seconds,
        direct_instructions=direct_instructions,
        pal_instructions=pal_instructions,
        direct_max_output_tokens=args.direct_max_output_tokens,
        pal_max_output_tokens=args.pal_max_output_tokens,
        reasoning_effort=args.reasoning_effort,
    )

    if args.retry_from_results:
        summary["retry_from_results"] = args.retry_from_results
        summary["retry_filter"] = args.retry_filter

    payload = {
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved detailed results to: {args.output}")


if __name__ == "__main__":
    main()
