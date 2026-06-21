"""读取 market.db 中的 DragonCore 市场数据。"""
import sqlite3
import base64
import re
import sys
from collections import Counter

DB_PATH = "db/market.db"


def strip_mc_color(text: str) -> str:
    return re.sub(r"§.", "", text)


# § 在 Java 序列化中是 UTF-8 编码 \xc2\xa7
_S = b"\xc2\xa7"


def extract_items(data: bytes) -> list[dict]:
    """从 Java 序列化数据中提取 DragonCore 市场物品。
    lore 格式 (UTF-8): §f出售人:§eXXX  §f售价:§eXXX铜钱  §f上架时间:§bXXX
    """
    items = []
    for m in re.finditer(rb'"market:ware_uid":\s*"([^"]+)"', data):
        uid = m.group(1).decode()
        start = max(0, m.start() - 4000)
        end = min(len(data), m.end() + 500)
        ctx = data[start:end]

        # 出售人:§eXXX
        seller_m = re.search(re.escape(_S) + rb'f[^\xc2]*?\xe5\x87\xba\xe5\x94\xae\xe4\xba\xba:' + re.escape(_S) + rb'e([A-Za-z0-9_]+)', ctx)
        seller = seller_m.group(1).decode() if seller_m else "?"

        # 售价:§eXXX铜钱
        price_m = re.search(re.escape(_S) + rb'e([\d,.]+)\xe9\x93\x9c\xe9\x92\xb1', ctx)
        price = price_m.group(1).decode() if price_m else "?"

        # 上架时间:§bXXX
        date_m = re.search(re.escape(_S) + rb'b(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', ctx)
        date = date_m.group(1).decode() if date_m else "?"

        # 物品翻译 key
        name_keys = re.findall(rb'pl\.item\.name\.([a-z0-9_]+)', ctx)
        # 自定义物品 ID
        item_id_m = re.search(rb'"equipment_att:item_id":\s*"([^"]+)"', ctx)
        # Minecraft 物品类型
        mc_m = re.search(rb'(minecraft:[a-z_]+)', ctx)
        # 附魔
        ench_raw = re.findall(rb'(minecraft:[a-z_]+)uq', ctx)
        enchants = list(set(e.decode() for e in ench_raw if b"binding" not in e))

        item_key = name_keys[0].decode() if name_keys else ""
        item_id = item_id_m.group(1).decode() if item_id_m else ""
        mc_type = mc_m.group(1).decode() if mc_m else ""

        items.append({
            "uid": uid[:12] + "...",
            "seller": seller,
            "price": price,
            "date": date,
            "item_key": item_key,
            "item_id": item_id or item_key or mc_type,
            "enchants": enchants,
        })

    return items


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"数据库表: {tables}\n")

    for table in tables:
        cursor.execute(f'SELECT data FROM "{table}"')
        for row in cursor.fetchall():
            value = row[0]
            if not isinstance(value, str) or len(value) < 100:
                print(f"{table}: {value}")
                continue

            decoded = base64.b64decode(value)
            items = extract_items(decoded)
            print(f"=== 市场数据 ({len(items)} 件物品) ===\n")

            # 卖家统计
            sellers = Counter(i["seller"] for i in items if i["seller"] != "unknown")
            print(f"卖家排行 ({len(sellers)} 人):")
            for name, count in sellers.most_common(20):
                print(f"  {name}: {count} 件")
            print()

            # 最新交易
            dated = [i for i in items if i["date"] != "unknown"]
            dated.sort(key=lambda x: x["date"], reverse=True)
            print(f"最新 {min(20, len(dated))} 笔:")
            for i in dated[:20]:
                print(f"  {i['date']}  {i['seller']:16s}  {i['price']:>10s}  {i['item_id']}")
            print()

            # 物品统计
            item_ids = Counter(i["item_id"] for i in items if i["item_id"])
            print(f"物品种类 ({len(item_ids)} 种):")
            for name, count in item_ids.most_common(20):
                print(f"  {name}: {count} 件")

    conn.close()


if __name__ == "__main__":
    main()
