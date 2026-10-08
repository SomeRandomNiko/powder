// armer.exe - keeps hook_guard.dll's guarded page armed until the first TLS handshake is caught, then
// patches ssl_verify_cert_chain to `mov eax,1; ret` (play build).
//
//   armer.exe --pid <Steep pid> --log <path to hook_guard.log>
//
// PAGE_GUARD is one-shot, and VMProtect blocks the game's own VirtualProtect on its code, so several
// threads re-arm the page from outside (VirtualProtectEx) without gaps. "hook hit" in the log means a
// handshake went through the trap; by then the boot integrity check is over, so the function can be
// byte-patched safely and arming stops. Exit codes: 0 patched, 2 game exited first, 1 error.
//
//   zig cc -target x86_64-windows-gnu -O2 -o bin/armer.exe armer.c

#include <windows.h>
#include <tlhelp32.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TARGET_RVA 0x1D3E290ULL
#define NTHREADS 4

static volatile LONG64 g_addr = 0;
static HANDLE g_proc = NULL;
static volatile LONG g_stop = 0;
static const char *g_log = NULL;

static int log_has(const char *needle) {
    FILE *f = fopen(g_log, "rb");
    if (!f) return 0;
    static char buf[65536];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f); buf[n] = 0; fclose(f);
    return strstr(buf, needle) != NULL;
}

static int alive(void) {
    DWORD ec;
    return GetExitCodeProcess(g_proc, &ec) && ec == STILL_ACTIVE;
}

static uintptr_t module_base(DWORD pid) {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    MODULEENTRY32 me; me.dwSize = sizeof(me);
    uintptr_t base = 0;
    if (Module32First(snap, &me)) base = (uintptr_t)me.modBaseAddr;   // first module = the exe
    CloseHandle(snap);
    return base;
}

static DWORD WINAPI spin(LPVOID p) {
    (void)p;
    DWORD old; void *addr = (void *)(uintptr_t)g_addr;
    while (!g_stop) VirtualProtectEx(g_proc, addr, 0x1000, PAGE_EXECUTE_READ | PAGE_GUARD, &old);
    return 0;
}

int main(int argc, char **argv) {
    DWORD pid = 0;
    for (int i = 1; i + 1 < argc; i++) {
        if (!strcmp(argv[i], "--pid")) pid = (DWORD)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--log")) g_log = argv[++i];
    }
    if (!pid || !g_log) { printf("usage: armer.exe --pid <pid> --log <hook_guard.log>\n"); return 1; }
    g_proc = OpenProcess(PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION,
                         FALSE, pid);
    if (!g_proc) { printf("armer: OpenProcess(%lu) failed %lu\n", pid, GetLastError()); return 1; }

    // the VEH must exist before the guard is armed, or a guard fault would crash the game
    while (!log_has("attach")) { if (!alive()) return 2; Sleep(150); }
    uintptr_t base = 0;
    while (!(base = module_base(pid))) { if (!alive()) return 2; Sleep(200); }
    g_addr = (LONG64)(base + TARGET_RVA);
    printf("armer: pid %lu target 0x%llx, arming\n", pid, (unsigned long long)g_addr); fflush(stdout);

    HANDLE threads[NTHREADS - 1]; int nthreads = 0;
    for (int i = 1; i < NTHREADS; i++) {
        HANDLE t = CreateThread(NULL, 0, spin, NULL, 0, NULL);
        if (t) threads[nthreads++] = t;
    }
    DWORD old; void *addr = (void *)(uintptr_t)g_addr; unsigned c = 0;
    for (;;) {
        VirtualProtectEx(g_proc, addr, 0x1000, PAGE_EXECUTE_READ | PAGE_GUARD, &old);
        if ((++c & 0x7FF) == 0) {
            if (log_has("hook hit")) break;
            if (!alive()) { g_stop = 1; printf("armer: game exited\n"); return 2; }
        }
    }
    // stop re-arming and wait until every re-arming thread has really finished: one that was paused
    // could otherwise put the guard back after it is taken off below
    g_stop = 1;
    if (nthreads) WaitForMultipleObjects((DWORD)nthreads, threads, TRUE, INFINITE);
    for (int i = 0; i < nthreads; i++) CloseHandle(threads[i]);
    VirtualProtectEx(g_proc, addr, 0x1000, PAGE_EXECUTE_READ, &old);          // guard off
    static const unsigned char patch[6] = {0xB8, 0x01, 0x00, 0x00, 0x00, 0xC3};  // mov eax,1 ; ret
    DWORD oldp;
    if (!VirtualProtectEx(g_proc, addr, 6, PAGE_EXECUTE_READWRITE, &oldp)) {
        printf("armer: VirtualProtectEx(RWX) failed %lu\n", GetLastError()); return 1;
    }
    SIZE_T wr = 0;
    BOOL ok = WriteProcessMemory(g_proc, addr, patch, 6, &wr);
    VirtualProtectEx(g_proc, addr, 6, PAGE_EXECUTE_READ, &oldp);
    FlushInstructionCache(g_proc, addr, 6);
    unsigned char chk[6] = {0}; SIZE_T rd = 0;
    ReadProcessMemory(g_proc, addr, chk, 6, &rd);
    int good = ok && wr == 6 && !memcmp(chk, patch, 6);
    printf("armer: patch %s\n", good ? "ok" : "FAILED"); fflush(stdout);
    return good ? 0 : 1;
}
