"""
职位检索引擎 — 基于 encryptExpectId 增量遍历推荐数据

根据流量日志分析，Boss直聘的精准匹配机制：
  1. /wapi/zpgeek/pc/recommend/expect/list.json → 获取用户预设的期望列表（encryptExpectId）
  2. /wapi/zpgeek/pc/recommend/job/list.json?encryptExpectId=xxx → 获取高匹配度职位

策略：
  - 获取用户所有的求职期望（encryptExpectId）
  - 切换不同的 encryptExpectId 获取不同维度的精准推荐
  - 增量去重避免重复沟通

用法:
    from src.spider_engine import SpiderEngine
    engine = SpiderEngine(page, store)
    for job in engine.search():
        print(job["job_title"])
"""

import time
import random
import json
from typing import Generator, Optional
from DrissionPage import ChromiumPage
from src.config import config
from src.fingerprint_store import FingerprintStore
from src.lark_notifier import LarkNotifier


class SpiderEngine:
    """增量职位检索引擎（encryptExpectId 驱动）"""

    # API 端点
    _EXPECT_API = (
        "https://www.zhipin.com/wapi/zpgeek/pc/recommend/expect/list.json"
    )
    _LIST_API = (
        "https://www.zhipin.com/wapi/zpgeek/pc/recommend/job/list.json"
    )

    def __init__(
        self,
        page: ChromiumPage,
        store: FingerprintStore,
        notifier: LarkNotifier | None = None,
    ):
        self.page = page
        self.store = store
        self.notifier = notifier or LarkNotifier()
        self._stats = {"pages_scanned": 0, "jobs_found": 0, "new_jobs": 0}

        # 从 search_url 中提取 city
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(config.search_url)
        qs = parse_qs(parsed.query)
        self._city = qs.get("city", ["101020100"])[0]

    # ── 公开接口 ──

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def search(self) -> Generator[dict, None, None]:
        """
        先访问首页/搜索页触发SPA加载上下文，再通过API拉取多页推荐数据。

        流程：
          1. 访问搜索页（带search_url参数），让SPA加载用户上下文
          2. 调用API拉取多页推荐列表（page=1~3+）
          3. 全局增量去重，发现足够新职位后停止
        """
        self._stats = {"pages_scanned": 0, "jobs_found": 0, "new_jobs": 0}

        # 第1步：访问首页，触发SPA完整加载（求职期望在首页/推荐页初始化）
        print("[SpiderEngine] 🌐 访问首页，触发SPA加载求职期望...")
        try:
            self.page.get("https://www.zhipin.com/", timeout=15)
        except Exception as e:
            print(f"[SpiderEngine] ⚠️ 首页加载异常: {e}")
        time.sleep(random.uniform(3, 5))

        # 再访问搜索页（但保持SPA上下文）
        print("[SpiderEngine] 🌐 访问搜索页，加载推荐上下文...")
        try:
            self.page.get(config.search_url, timeout=15)
        except Exception as e:
            print(f"[SpiderEngine] ⚠️ 搜索页加载异常: {e}")
        time.sleep(random.uniform(3, 5))

        # 第2步：获取用户预设的全部求职期望
        expect_ids = self._fetch_expect_ids()

        # 如果用户有多个期望，优先遍历；否则使用空expectId遍历多页
        if not expect_ids:
            print("[SpiderEngine] ⚠️ 未获取到求职期望，使用默认推荐遍历多页")
            expect_ids = [""]

        print(
            f"[SpiderEngine] 📋 获取到 {len(expect_ids)} 个求职期望"
        )

        # 第3步：遍历每个期望，拉取多页
        MAX_PAGES = max(config.pages_to_scan, 5)  # 至少5页

        for idx, expect_id in enumerate(expect_ids):
            consecutive_empty = 0  # 初始化连续空计数
            expect_label = expect_id[:16] + "..." if len(expect_id) > 16 else expect_id
            print(
                f"[SpiderEngine] 🔄 遍历 ({idx+1}/{len(expect_ids)}): "
                f"encryptExpectId={expect_label}"
            )

            for page_num in range(1, MAX_PAGES + 1):
                jobs = self._fetch_page(page_num, expect_id)
                if not jobs:
                    break

                self._stats["pages_scanned"] += 1
                self._stats["jobs_found"] += len(jobs)

                # 增量统计
                new_count = 0
                for job in jobs:
                    is_new = self.store.add_or_skip(
                        job["fingerprint"],
                        job_title=job["job_title"],
                        company_name=job["company_name"],
                        salary=job["salary"],
                        job_url=job.get("url", ""),
                    )
                    if is_new:
                        self._stats["new_jobs"] += 1
                        new_count += 1
                        yield job

                print(
                    f"[SpiderEngine] 📄 页 {page_num}: {len(jobs)} 个, "
                    f"{new_count} 个新职位"
                )

                # 如果连续2页无新职位，提前终止
                if new_count == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

                if consecutive_empty >= 2:
                    print(
                        f"[SpiderEngine] ⏹ 连续 {consecutive_empty} 页无新职位，终止"
                    )
                    break

                # 页间延迟
                if page_num < MAX_PAGES:
                    time.sleep(random.uniform(2, 4))

            # 期望间延迟
            if idx < len(expect_ids) - 1:
                time.sleep(random.uniform(1.5, 3))

        # 持久化
        self.store.save()
        print(
            f"[SpiderEngine] 📊 本轮统计: "
            f"扫描 {self._stats['pages_scanned']} 页, "
            f"发现 {self._stats['jobs_found']} 个职位, "
            f"其中 {self._stats['new_jobs']} 个新职位"
        )

        if self._stats["jobs_found"] == 0:
            self.notifier.send_alert(
                "零结果警告",
                "所有求职期望均未找到推荐职位",
            )

    # ── API 方法 ──

    def _fetch_expect_ids(self) -> list[str]:
        """
        获取用户预设的全部求职期望。

        调用 /wapi/zpgeek/pc/recommend/expect/list.json
        返回 encryptExpectId 列表。
        """
        try:
            resp_json = self.page.run_js(f"""
                try {{
                    const xhr = new XMLHttpRequest();
                    xhr.open('GET', '{self._EXPECT_API}', false);
                    xhr.withCredentials = true;
                    xhr.send();
                    return xhr.responseText;
                }} catch(e) {{ return ''; }}
            """)

            if not resp_json:
                return []

            data = json.loads(resp_json) if isinstance(resp_json, str) else resp_json
            if data.get("code") != 0:
                print(f"[SpiderEngine] 🔍 expect API code={data.get('code')}: {data.get('msg','')}")
                return []

            zpdata = data.get("zpData") or {}
            # 调试：输出API返回的顶层key
            if zpdata:
                keys = [k for k in zpdata.keys() if not k.startswith('_')]
                print(f"[SpiderEngine] 🔍 expect API keys: {keys[:10]}")

            expect_list = zpdata.get("list") or zpdata.get("expectList") or []

            # 打印完整原始数据调试
            if expect_list:
                import pprint
                print(f"[SpiderEngine] 🔍 expectList 原始数据:")
                for i, item in enumerate(expect_list):
                    print(f"[SpiderEngine] 🔍   [{i}] type={type(item).__name__}: {json.dumps(item, ensure_ascii=False)[:300]}")

            expect_ids = []
            for item in expect_list:
                if isinstance(item, dict):
                    # 从原始数据确认字段名：encryptId, positionName
                    eid = str(
                        item.get("encryptId")  # 实际字段名
                        or item.get("encryptExpectId")
                        or item.get("expectId")
                        or ""
                    )
                    name = (
                        item.get("positionName")  # 实际字段名
                        or item.get("name")
                        or item.get("jobName")
                        or ""
                    )
                    if eid:
                        expect_ids.append(eid)
                        print(
                            f"[SpiderEngine] 📌 期望: '{name}' "
                            f"(encryptId={eid[:20]}...)"
                        )
                    else:
                        print(f"[SpiderEngine] ⚠️  期望[{len(expect_ids)}] 无encryptId, keys={list(item.keys())[:10]}")
                elif isinstance(item, str):
                    expect_ids.append(item)

            return expect_ids

        except Exception as e:
            print(f"[SpiderEngine] 期望列表获取异常: {e}")
            return []

    def _fetch_page(self, page_num: int, expect_id: str) -> list[dict]:
        """获取某一页推荐职位"""
        api_url = self._build_list_url(page_num, expect_id)

        try:
            resp_json = self.page.run_js(f"""
                try {{
                    const xhr = new XMLHttpRequest();
                    xhr.open('GET', '{api_url}', false);
                    xhr.withCredentials = true;
                    xhr.setRequestHeader('Accept', 'application/json');
                    xhr.send();
                    return xhr.responseText;
                }} catch(e) {{ return ''; }}
            """)

            if not resp_json:
                return []

            data = json.loads(resp_json) if isinstance(resp_json, str) else resp_json
            if data.get("code") != 0:
                return []

            zpdata = data.get("zpData") or {}
            job_list = zpdata.get("list") or zpdata.get("jobList") or []
            if not isinstance(job_list, list) or len(job_list) == 0:
                return []

            lid = zpdata.get("lid", "")
            jobs = []
            for item in job_list:
                job = self._parse_api_item(item, lid)
                if job:
                    jobs.append(job)

            return jobs

        except Exception as e:
            print(f"[SpiderEngine] API异常: {e}")
            return []

    def _build_list_url(self, page_num: int, expect_id: str) -> str:
        """构造列表API URL"""
        params = {
            "page": page_num,
            "pageSize": 15,
            "city": self._city,
        }

        if expect_id:
            params["encryptExpectId"] = expect_id

        parts = [f"{k}={v}" for k, v in params.items()]
        return f"{self._LIST_API}?{'&'.join(parts)}"

    def _parse_api_item(self, item: dict, lid: str = "") -> Optional[dict]:
        """解析 API 返回的单个职位项"""
        job_title = (
            item.get("jobName")
            or item.get("job_name")
            or item.get("jobTitle")
            or ""
        )
        company_name = (
            item.get("brandName")
            or item.get("brand_name")
            or item.get("companyName")
            or ""
        )
        salary = (
            item.get("salaryDesc")
            or item.get("salary_desc")
            or item.get("salaryName")
            or ""
        )

        if not job_title:
            return None

        security_id = item.get("securityId") or ""
        job_id = item.get("jobId") or ""

        url = ""
        if security_id:
            url = (
                f"https://www.zhipin.com/web/geek/job?"
                f"securityId={security_id}&lid={lid}"
            )

        fingerprint = FingerprintStore.make_fingerprint(
            job_title, company_name, salary,
        )

        return {
            "job_title": job_title,
            "company_name": company_name,
            "salary": salary,
            "url": url,
            "fingerprint": fingerprint,
            "securityId": security_id,
            "jobId": job_id,
            "lid": lid,
        }