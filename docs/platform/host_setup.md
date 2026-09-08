# 호스트 설정 — **판이 밤새 도는 기계를 만드는 법**

> 이 문서는 코드가 아니라 **Windows 쪽 설정**을 다룬다. 리포지터리 밖이라 자동으로
> 적용되지 않고, 새 기계에 옮길 때 사람이 다시 해야 한다.

---

## 왜 이 문서가 있나

2026-08-20 06:47 에 **Windows 가 WSL 을 업데이트하며 VM 을 내렸다.** 그때 판 8개가
죽었고, 포지션 4개 중 3개가 거래소 조건부 손절 없이 남았다.

```
06:47:41  [43]   설치 시작 — MicrosoftCorporationII.WindowsSubsystemforLinux
06:47:41  [7040] "Linux용 Windows 하위 시스템 서비스" 시작 유형 → 사용 안 함
06:47:46         ← API 로그 마지막 줄
06:48:30         WSL 커널 새로 부팅
```

전말: [live_issues/2026-08-20](../incidents/2026-08-20_wsl_update_killed_the_backend.md)

⚠️ **이 설정은 사고를 줄일 뿐 없애지 못한다.** 없애는 것은 거래소 조건부 손절이고,
그것이 코드 쪽에서 한 일이다. 여기 있는 것은 **덜 자주 겪게** 하는 것이다.

---

## 1. Store 앱 자동 업데이트를 끈다 (이번 사고의 직접 원인)

WSL 은 이제 **Microsoft Store 앱**으로 배포된다. Windows Update 본체가 아니라 Store 가
예고 없이 갈아 끼운다.

### 적용 (관리자 PowerShell)

```powershell
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' `
  -Name 'AutoDownload' -Value 2 -PropertyType DWord -Force | Out-Null
```

`2` 가 **사용 안 함**이다.

### 확인

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' | Select-Object AutoDownload
```

### 되돌리기

```powershell
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' `
  -Name 'AutoDownload' -ErrorAction SilentlyContinue
```

### 화면으로 하려면

```
gpedit.msc → 컴퓨터 구성 → 관리 템플릿 → Windows 구성 요소 → 스토어
           → "앱 자동 다운로드 및 설치 해제" → 사용
```

또는 `Microsoft Store → 프로필 → 설정 → 앱 자동 업데이트` 끄기 (정책보다 약하다 —
사람이 다시 켤 수 있다).

⚠️ **보안 업데이트도 같이 멈춘다.** 정기적으로 Store 에서 손으로 갱신하되, **판이
안 도는 시간에** 한다.

---

## 2. Windows 재부팅 시간대를 판이 안 도는 쪽으로

Store 를 막아도 **OS 누적 업데이트의 재부팅은 별개**다. 활성 시간 안에는 재부팅하지
않으므로, 그 창을 **판이 도는 시간**에 맞춘다.

```
설정 → Windows 업데이트 → 고급 옵션 → 활성 시간
```

🔴 **기본값(08~17시)은 우리에게 정반대다.** 코인은 24시간 돌고 밤에도 매매가 난다 —
실제로 이번 사고가 새벽 6시 47분이었다.

⚠️ 활성 시간은 최대 18시간까지만 지정할 수 있다. 완전히 막는 수단이 아니다.

---

## 3. Docker Desktop 자동 시작 · 자동 업데이트

```
Docker Desktop → Settings → General
   ✅ Start Docker Desktop when you sign in     ← 켠다 (기계가 켜지면 판도 돈다)
   ⬜ Automatically check for updates            ← 끈다 (WSL 과 같은 이유)
```

⚠️ Docker Desktop 도 WSL 위에 산다. WSL 이 내려가면 컨테이너도 같이 내려가고, WSL 이
돌아오면 `restart: unless-stopped` 가 데려온다 — **그 사이 1~2분은 공백이다.**

---

## 4. 그래도 남는 것

| 층 | 무엇이 죽어도 사나 | 어떻게 |
|---|---|---|
| **거래소 조건부 손절** | 기계가 통째로 죽어도 | 코드 (`_guard_stop`) ← **진짜 방어선** |
| 컨테이너 재시작 | 프로세스 · WSL VM | `restart: unless-stopped` |
| 호스트 설정 | 예고 없는 업데이트 | 이 문서 |
| 리눅스 서버 | Windows Update 자체 | 아직 안 함 |

🔴 **위에서 아래로 갈수록 약하다.** 이 문서의 설정은 가장 약한 층이다 — 없는 것보다
낫지만, 이것만 믿으면 안 된다.

⬜ **진짜 배포는 리눅스 서버다.** Windows Update 가 없고, `systemd` 가 부팅 때 컨테이너를
띄운다. 월 몇 달러면 되고, 그때 이 문서의 1~3번은 필요 없어진다.

---

## 관련

- [2026-08-20 사고](../incidents/2026-08-20_wsl_update_killed_the_backend.md) — 이 문서가 생긴 이유
- [운영 방법](../../README.md) — `make up` 으로 전부 띄우기
