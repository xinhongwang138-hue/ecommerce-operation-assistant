import { proxyCsvDownload } from "@/lib/export-proxy";

/** 下载商品清单 CSV。 */
export async function GET() {
  return proxyCsvDownload("/api/v1/exports/products.csv", "products.csv");
}
