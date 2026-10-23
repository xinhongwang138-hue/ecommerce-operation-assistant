"use client";

import * as echarts from "echarts";
import { useEffect, useRef } from "react";

type TrendPoint = { date: string; count: number };
type RingSlice = { name: string; value: number };

type DashboardChartsProps = {
  trend: TrendPoint[];
  ring: RingSlice[];
  trendTitle?: string;
  trendSubtitle?: string;
  ringTitle?: string;
  ringSubtitle?: string;
  badgeText?: string;
};

function readTheme(): "light" | "dark" {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function chartColors(theme: "light" | "dark") {
  return {
    grid: theme === "dark" ? "#2b2a40" : "#e9e9f4",
    text: theme === "dark" ? "#9b98b4" : "#6b7280",
    axis: theme === "dark" ? "#9b98b4" : "#6b7280",
    split: theme === "dark" ? "#2b2a40" : "#e9e9f4",
  };
}

/** 渲染 TrendLineChart 组件，任务/商品趋势折线图。 */
function TrendLineChart({ points }: { points: TrendPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;

    const render = () => {
      const theme = readTheme();
      const colors = chartColors(theme);
      chart.setOption(
        {
          grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
          tooltip: { trigger: "axis" },
          xAxis: {
            type: "category",
            boundaryGap: false,
            data: points.map((point) => point.date),
            axisLine: { lineStyle: { color: colors.axis } },
            axisLabel: { color: colors.text, fontSize: 11 },
            axisTick: { show: false },
          },
          yAxis: {
            type: "value",
            minInterval: 1,
            axisLabel: { color: colors.text, fontSize: 11 },
            splitLine: { lineStyle: { color: colors.split } },
          },
          series: [
            {
              name: "任务数",
              type: "line",
              smooth: true,
              symbol: "circle",
              symbolSize: 6,
              data: points.map((point) => point.count),
              lineStyle: { width: 3, color: "#8b5cf6" },
              itemStyle: { color: "#6d5ae6" },
              areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                  { offset: 0, color: "rgba(139, 92, 246, 0.32)" },
                  { offset: 1, color: "rgba(139, 92, 246, 0.02)" },
                ]),
              },
            },
          ],
        },
        true,
      );
    };

    render();
    const observer = new MutationObserver(render);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [points]);

  return <div ref={ref} className="echart-container h-64 w-full" />;
}

/** 渲染 DonutRingChart 组件，任务状态占比环形图。 */
function DonutRingChart({ slices }: { slices: RingSlice[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;

    const render = () => {
      const theme = readTheme();
      const colors = chartColors(theme);
      const palette: Record<string, string> = {
        成功: "#10b981",
        失败: "#ef4444",
        运行中: "#6d5ae6",
        排队中: "#8b5cf6",
        已取消: "#94a3b8",
      };
      chart.setOption(
        {
          tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
          legend: {
            bottom: 0,
            icon: "circle",
            textStyle: { color: colors.text, fontSize: 11 },
          },
          series: [
            {
              name: "任务状态",
              type: "pie",
              radius: ["55%", "80%"],
              center: ["50%", "42%"],
              avoidLabelOverlap: true,
              itemStyle: { borderRadius: 8, borderColor: theme === "dark" ? "#191827" : "#ffffff", borderWidth: 2 },
              label: { show: false },
              emphasis: { label: { show: true, fontWeight: "bold", fontSize: 12 } },
              labelLine: { show: false },
              data: slices.map((slice) => ({
                ...slice,
                itemStyle: { color: palette[slice.name] ?? "#8b5cf6" },
              })),
            },
          ],
        },
        true,
      );
    };

    render();
    const observer = new MutationObserver(render);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [slices]);

  return <div ref={ref} className="echart-container h-64 w-full" />;
}

/** 渲染 DashboardCharts 组件，首页仪表盘折线图 + 环形图组合。 */
export function DashboardCharts({
  trend,
  ring,
  trendTitle = "任务趋势",
  trendSubtitle = "近 7 日生成任务启动数",
  ringTitle = "任务状态分布",
  ringSubtitle = "生成任务各状态占比",
  badgeText = "近 7 天",
}: DashboardChartsProps) {
  return (
    <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
      <div className="card p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-[var(--foreground)]">{trendTitle}</h3>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">{trendSubtitle}</p>
          </div>
          <span className="rounded-full bg-[var(--primary-soft)] px-2.5 py-1 text-[11px] font-semibold text-[var(--primary)]">{badgeText}</span>
        </div>
        <div className="mt-4">
          {trend.length ? (
            <TrendLineChart points={trend} />
          ) : (
            <div className="flex h-64 items-center justify-center text-sm text-[var(--muted-foreground)]">暂无趋势数据</div>
          )}
        </div>
      </div>

      <div className="card p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-[var(--foreground)]">{ringTitle}</h3>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">{ringSubtitle}</p>
          </div>
        </div>
        <div className="mt-4">
          {ring.length ? (
            <DonutRingChart slices={ring} />
          ) : (
            <div className="flex h-64 items-center justify-center text-sm text-[var(--muted-foreground)]">暂无分布数据</div>
          )}
        </div>
      </div>
    </div>
  );
}
