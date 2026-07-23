"""Performance telemetry collection via CDP and in-page observers."""

from __future__ import annotations

from typing import Any, Dict, List

from playwright.async_api import Page

from .defects import DefectTracker


class PerformanceMonitor:
    """Collects long-task and memory telemetry through CDP and in-page observers."""

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self.cdp: Any = None

    async def install(self, page: Page) -> None:
        if self.cdp is not None:
            return
        self.cdp = await page.context.new_cdp_session(page)
        await self.cdp.send("Performance.enable")
        try:
            await self.cdp.send("Page.enable")
        except Exception:
            pass
        await page.add_init_script(
            """
            () => {
                window.__deepLongTasks = [];
                try {
                    const obs = new PerformanceObserver(list => {
                        for (const entry of list.getEntries()) {
                            window.__deepLongTasks.push({
                                startTime: entry.startTime,
                                duration: entry.duration,
                                name: entry.name || 'longtask'
                            });
                        }
                    });
                    obs.observe({ type: 'longtask', buffered: true });
                } catch (e) {}
            }
            """
        )

    async def snapshot(self, page: Page) -> Dict[str, Any]:
        metrics = await self.cdp.send("Performance.getMetrics") if self.cdp else {"metrics": []}
        navigation: Dict[str, Any] = {"entries": []}
        if self.cdp:
            try:
                history = await self.cdp.send("Page.getNavigationHistory")
                entries = history.get("entries", [])
                navigation = {
                    "current_index": history.get("currentIndex", -1),
                    "entries": [
                        {
                            "id": item.get("id"),
                            "url": item.get("url"),
                            "title": item.get("title"),
                            "transition_type": item.get("transitionType"),
                        }
                        for item in entries
                    ],
                }
            except Exception as exc:
                navigation = {"entries": [], "error": str(exc)}
        memory = await page.evaluate(
            """() => {
                const mem = performance.memory || {};
                return {
                    usedJSHeapSize: mem.usedJSHeapSize || 0,
                    totalJSHeapSize: mem.totalJSHeapSize || 0,
                    jsHeapSizeLimit: mem.jsHeapSizeLimit || 0,
                };
            }"""
        )
        long_tasks = await page.evaluate("() => window.__deepLongTasks || []")
        fps = await page.evaluate(
            """() => new Promise(resolve => {
                const start = performance.now();
                let frames = 0;
                function tick(now) {
                    frames += 1;
                    if (now - start >= 600) {
                        const fps = frames / ((now - start) / 1000);
                        resolve({ fps });
                        return;
                    }
                    requestAnimationFrame(tick);
                }
                requestAnimationFrame(tick);
            })"""
        )
        return {
            "metrics": metrics.get("metrics", []),
            "navigation": navigation,
            "memory": memory,
            "long_tasks": long_tasks,
            "fps": fps,
        }

    async def detect_bottlenecks(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any],
        step_num: int,
        action: str,
        url: str,
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        before_heap = before.get("memory", {}).get("usedJSHeapSize", 0)
        after_heap = after.get("memory", {}).get("usedJSHeapSize", 0)
        heap_delta = after_heap - before_heap
        if heap_delta > 30_000_000:
            findings.append(
                {
                    "step": step_num,
                    "type": "memory-spike",
                    "action": action,
                    "heap_delta_bytes": heap_delta,
                    "url": url,
                }
            )

        before_long_count = len(before.get("long_tasks", []))
        new_long_tasks = after.get("long_tasks", [])[before_long_count:]
        for task in new_long_tasks:
            duration = task.get("duration", 0)
            if duration > 50:
                findings.append(
                    {
                        "step": step_num,
                        "type": "long-task",
                        "action": action,
                        "duration_ms": duration,
                        "url": url,
                    }
                )
            if duration > 2000:
                findings.append(
                    {
                        "step": step_num,
                        "type": "main-thread-blocked",
                        "action": action,
                        "duration_ms": duration,
                        "url": url,
                    }
                )

        fps = after.get("fps", {}).get("fps", 60)
        if fps < 20:
            findings.append(
                {
                    "step": step_num,
                    "type": "fps-drop",
                    "action": action,
                    "fps": fps,
                    "url": url,
                }
            )

        for finding in findings:
            self.defects.add("performance_bottlenecks", finding)
        return findings
