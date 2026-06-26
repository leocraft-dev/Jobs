"""
职位指纹持久化 — 本地 JSON 文件存储已处理职位指纹

职责：
  - 新增 / 查询 / 批量导入指纹
  - 自动加载与增量写入
  - 支持增量提前终止判定（连续 N 个已存在则判定无增量）

用法:
    from src.fingerprint_store import FingerprintStore
    
    store = FingerprintStore()
    store.add("abc123", job_title="Python开发", company_name="XX科技", salary="20-30K")
    if store.exists("abc123"):
        print("已存在")
    should_stop = store.check_early_termination(new_fingerprints, threshold=5)
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from typing import Optional
from src.config import config


class FingerprintStore:
    """职位指纹存储（线程安全级别：单线程）"""

    def __init__(self, path: str | None = None):
        self._path = path or config.fingerprint_path
        self._data: dict[str, dict] = {}
        self._dirty: bool = False
        self._load()

    # ── 公开属性 ──

    @property
    def count(self) -> int:
        """当前存储指纹总数"""
        return len(self._data)

    @property
    def path(self) -> str:
        """存储文件路径"""
        return self._path

    # ── 核心 CRUD ──

    def add(
        self,
        fingerprint: str,
        job_title: str = "",
        company_name: str = "",
        salary: str = "",
        job_url: str = "",
        status: str = "new",
    ) -> bool:
        """
        添加新指纹（如果已存在则跳过）
        返回 True 表示真正新增，False 表示已存在
        """
        if fingerprint in self._data:
            return False

        self._data[fingerprint] = {
            "fingerprint": fingerprint,
            "job_title": job_title,
            "company_name": company_name,
            "salary": salary,
            "job_url": job_url,
            "status": status,
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._dirty = True
        return True

    def exists(self, fingerprint: str) -> bool:
        """检查指纹是否已存在"""
        return fingerprint in self._data

    def get(self, fingerprint: str) -> dict | None:
        """获取单个指纹记录"""
        return self._data.get(fingerprint)

    def update_status(self, fingerprint: str, status: str) -> bool:
        """更新指纹状态（new / contacted / skipped）"""
        record = self._data.get(fingerprint)
        if record is None:
            return False
        record["status"] = status
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._dirty = True
        return True

    def add_or_skip(self, fingerprint: str, **kwargs) -> bool:
        """
        添加指纹（如果已存在则跳过）
        与 add() 的区别：返回 True 表示处理过（不论新增还是跳过）
        """
        if self.exists(fingerprint):
            return False  # 已存在，跳过
        self.add(fingerprint, **kwargs)
        return True  # 新指纹

    # ── 指纹生成 ──

    @staticmethod
    def make_fingerprint(job_title: str, company_name: str, salary: str) -> str:
        """根据职位关键信息生成 MD5 指纹"""
        raw = f"{job_title}_{company_name}_{salary}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    # ── 增量提前终止判定 ──

    def check_early_termination(
        self,
        candidate_fingerprints: list[str],
        threshold: int = 5,
    ) -> bool:
        """
        判定是否应提前终止本轮检索

        在候选指纹列表中从前往后扫描，若连续 threshold 个指纹
        均已存在于库中，返回 True（终止检索）；否则 False。
        """
        consecutive_exists = 0
        for fp in candidate_fingerprints:
            if self.exists(fp):
                consecutive_exists += 1
                if consecutive_exists >= threshold:
                    return True
            else:
                consecutive_exists = 0
        return False

    # ── 持久化 ──

    def save(self, force: bool = False) -> bool:
        """将内存数据写入磁盘（仅在脏标记为 True 时写入）"""
        if not self._dirty and not force:
            return True

        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            self._dirty = False
            return True
        except (IOError, OSError) as e:
            print(f"[FingerprintStore] 写入失败: {e}")
            return False

    def _load(self) -> None:
        """从磁盘加载指纹数据"""
        if not os.path.isfile(self._path):
            self._data = {}
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            print(f"[FingerprintStore] 加载 {len(self._data)} 条指纹记录")
        except (json.JSONDecodeError, IOError) as e:
            print(f"[FingerprintStore] 加载失败: {e}，使用空库")
            self._data = {}

    def __enter__(self):
        """上下文管理器支持（自动保存）"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save()