"""mindgraph 自带配置（从环境变量 / .env 读取，无外部框架依赖）。

两个模型角色（见 SPEC.md §5）：
  - worker：抽取 + 回答（推荐 DeepSeek）。**唯一必需** key。
  - judge ：可选的独立判分模型（自治模式用），**必须不同于 worker**。

环境变量（优先用 KG_* 干净命名；为兼容宿主项目也回退老命名）：
  worker : KG_WORKER_API_KEY / KG_WORKER_BASE_URL / KG_WORKER_MODEL
  judge  : KG_JUDGE_API_KEY  / KG_JUDGE_BASE_URL  / KG_JUDGE_MODEL
  neo4j  : NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NEO4J_DATABASE
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _load_dotenv() -> None:
    """若装了 python-dotenv，则加载当前目录 .env；否则只用现有环境变量。"""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        pass


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v not in (None, ""):
            return v
    return default


@dataclass
class Settings:
    worker_api_key: str | None
    worker_base_url: str | None
    worker_model: str | None
    judge_api_key: str | None
    judge_base_url: str | None
    judge_model: str | None
    neo4j_uri: str | None
    neo4j_user: str | None
    neo4j_password: str | None
    neo4j_database: str | None

    @staticmethod
    def load() -> "Settings":
        _load_dotenv()
        return Settings(
            # worker（回退老命名 SF_* / MAIN_MODEL_NAME 便于从宿主项目迁移）
            worker_api_key=_env("KG_WORKER_API_KEY", "SF_API_KEY"),
            worker_base_url=_env("KG_WORKER_BASE_URL", "SF_BASE_URL"),
            worker_model=_env("KG_WORKER_MODEL", "KG_EXTRACTION_MODEL_NAME", "MAIN_MODEL_NAME"),
            # judge（可选；回退老命名 AL_BAILIAN_* / SUB_MODEL_NAME）
            judge_api_key=_env("KG_JUDGE_API_KEY", "AL_BAILIAN_API_KEY"),
            judge_base_url=_env("KG_JUDGE_BASE_URL", "AL_BAILIAN_BASE_URL"),
            judge_model=_env("KG_JUDGE_MODEL", "KG_JUDGE_MODEL_NAME", "SUB_MODEL_NAME"),
            neo4j_uri=_env("NEO4J_URI"),
            neo4j_user=_env("NEO4J_USER"),
            neo4j_password=_env("NEO4J_PASSWORD"),
            neo4j_database=_env("NEO4J_DATABASE"),
        )


settings = Settings.load()
