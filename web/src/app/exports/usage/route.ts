import { proxyCsvDownload } from "@/lib/export-proxy";

/** 下载用量明细 CSV。 */
export async function GET() {
  return proxyCsvDownload("/api/v1/exports/usage.csv", "usage.csv");
}
