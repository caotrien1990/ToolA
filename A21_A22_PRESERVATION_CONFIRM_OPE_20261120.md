# P16 A21/A22 - xac nhan ban sao bao ton (Ope, 20/11/2026)

Nguon tieu chi: P16_REMAINING_OPE_PACK_20261119.md L163-173. Nguon cross-check: P16_A21_G04_CROSSCHECK_20261119.md (muse1, VM) - 5/5 MATCH, da file 19/11.

## 1. A21 - ban sao bao ton P2, khop G-04

- Vi tri: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/41. CLAUDELTNAI/P16_PRESERVATION_20261119/ (tren dia, xac nhan 20/11).
- 4 file P2 + hash SHA-256 (byte, uppercase), da do lai 20/11 boi Ope:

| File | Size | SHA-256 do lai | G-04 (VM) | Ket qua |
|---|---|---|---|---|
| log_wtall.txt | 5550 B | 769964E209C3AB7EC8386A125C5EBD68AA44752C7815E1A383BCE1E6C04FE4EE | cung | MATCH |
| probe_a53.py | 576 B | 5BAC94786A74293BAC5B64E05BC8ADDDAC4ECF46EB48839B183AE064039E5330 | cung | MATCH |
| probe_sl.py | 783 B | 67B93730D506037321F53EB1E80D8BC8FA5F3AC8FF82F0F65A8FFD78F8345770 | cung | MATCH |
| runB.py | 702 B | BE5DAB92CC75964D76D443E206C2EE44D86A0243B44C30CE5706D2EF4697F6E9 | cung | MATCH |

- G-04 cross-check: KHOP 4/4 (size + hash) theo ho so muse1 da file. Tieu chi L167 (vi tri + hash ban sao P2 da luu va khop G-04) - DAP UNG.
- Nua con lai (can cu tai lieu) o muc B5 - KHONG thuoc ho so nay.

## 2. A22 - ban sao run_brfix_wt.ps1

- Vi tri: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/41. CLAUDELTNAI/P16_PRESERVATION_20261119/run_brfix_wt.ps1 (tren dia, xac nhan 20/11).
- Size 603 B; SHA-256 ED3A33E7ACE305B90633EE334BA0647131606C62B5254501041EC10FBBE3213D - khop G-07 (VM) theo ho so muse1 (5/5 MATCH).
- Tieu chi L173 (ban sao da luu; W7 khong con gi vi ly do bao ton) - phan ban sao DAP UNG; phan doi nhan thuoc campaign/muse1 (ho so muse1 L32-33), khong tu doi o day.

## 3. Ghi chu nguon

- worktree .tmp/wt-brB va wt-brfix khong con tren dia va run_brfix_wt.ps1 khong co trong git objects cua commit 58bfff4; ban sao bao ton da duoc luu truoc do (19/11) nen A21/A22 van khep duoc bang ho so + hash do lai, KHONG fabricate nguon da mat.
- Moi so lieu o muc 1-2 la do lai tu file that tren disk 20/11, khong chep mu ket qua cu.