# P16 A13 - Step-1 ho so muc tieu + D9 ho so pham vi (Ope, 20/11/2026)

Nguon tieu chi: P16_REMAINING_OPE_PACK_20261119.md L107-111. Khong co claim nao duoc dua ra moi; day la lap ho so, khong phai sua code.

## 1. Ho so Step-1 - muc tieu (O-28 / referred #9)

- Muc tieu Step-1: xac lap rang hanh vi MCP/T4 da duoc truy vet toi muc co the phan quyet duoc cho muc O-28, bang cach neu ten tung hanh vi da kiem tra va tung hanh vi nam ngoai pham vi. Khong mo rong sang kiem chung runtime moi.
- Boi canh: pack ghi ro (L109) khong claim nao duoc dua ra con tro toi ho so D9 co gioi han-pham vi o Sprint 8 Buoc 5, ho so Step-1 muc tieu chua bao gio duoc lap. Day la ho so Step-1 con no do.
- Trang thai truoc: Step-1 KHONG ton tai; D9 chi o dang con tro toi pham vi da gioi han trong Sprint 8 Buoc 5 (cung chua thi hanh - A18 L139). Ho so nay lap Step-1; phan D9 neu o muc 2.
- Pham vi muc tieu: (a) liet ke hanh vi MCP/T4 nao tuong duoc kiem tra trong cac lane truoc, (b) liet ke hanh vi nao bi loai khoi pham vi truy vet, (c) ghi ro day la ho so muc tieu/pham vi, KHONG phai ket qua kiem chung moi.

## 2. Ho so D9 - pham vi da gioi han (duoc neu ten)

D9 = MCP/T4 chua truy vet. Theo A18 L146, D9 la mot nhan Buoc 5: D9 (MCP/T4 chua truy vet - ho so pham vi da gioi han, muc A13). Pham vi gioi han duoc neu ten:

### Da nam trong pham vi kiem tra (truoc day)
1. A8 - probe chap nhan O-7 (pack L59, L63): probe chap nhan, da quyet, con no probe. Thuoc pham vi MCP/T4 vi di qua duong chap nhan/gateway.
2. T4 Sprint 3 + P16 phase (a)-(d) (theo bao cao T4 da file): kiem tra returns-shape tren cac tool T4/MCP (stateful page, DOM/CDP, generation gate) o muc schema/shape, khong phai runtime moi.
3. D8 (per-tool drops) - A18 L146: nhan Buoc 5 di cung D9; lien quan truc tiep toi viec tool nao duoc drop khoi returns-shape.

### Ngoai pham vi truy vet (khong duoc coi la da kiem tra)
1. T4 legs chua chay trong phase (d) nay (theo prompt giao viec: Khong thuoc prompt: T4 legs).
2. A5 run 2 (Codo, phase f) - lane khac.
3. A18 rollup (muse1) - lane khac.
4. W3-W12 ownership - lane/campaign khac.
5. F31 re-run / live test / restart P16 (e) - cho tin hieu rieng cua Anh; khong nam trong ho so pham vi da gioi han nay.
6. MCP stateful-T4 page sessions qua host MCP (durable-audit gap) - chi duoc neu ten la ngoai pham vi; khong kiem chung them.

## 3. Tieu chi xong (doi chieu L111)
- Hai ho so da luu: Step-1 (muc 1) + D9 (muc 2) - CO trong ho so nay.
- Pham vi kiem tra da neu ten: 3 muc trong pham vi + 6 muc ngoai pham vi - CO.
- Khong claim moi duoc dua ra; moi trich dan tu nguon da file (pack L107-111, L139, L146, L59/L63).