import { proxyCsvDownload } from "@/lib/export-proxy";

/** 下载绩效记录 CSV。 */
export async function GET() {
  return proxyCsvDownload("/api/v1/exports/performance-records.csv", "performance-records.csv");
}
