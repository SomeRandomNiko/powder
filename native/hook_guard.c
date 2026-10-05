// hook_guard.dll - makes Steep accept the server's TLS certificate (play build).
//
// Steep checks certificates with a statically linked OpenSSL (ssl_verify_cert_chain) and the exe is
// protected by VMProtect, which detects code patches and debug registers. So this DLL changes no code
// bytes: it traps execution of ssl_verify_cert_chain with a guarded memory page and a vectored
// exception handler that returns 1 ("chain OK"). PAGE_GUARD is one-shot and VMProtect blocks in-process
// VirtualProtect on its ranges, so armer.exe keeps re-arming the page from outside and, once the first
// TLS handshake was caught (the boot integrity check is over by then), patches the function to
// `mov eax,1; ret` and exits.
//
// Injected by the launcher (LoadLibraryW via CreateRemoteThread). Logs to hook_guard.log next to this
// DLL; armer.exe and the launcher read "attach" / "hook hit" from it.
//
// Only valid for the Steep.exe build whose ssl_verify_cert_chain is at image base + TARGET_RVA; the
// launcher checks the exe's SHA-256 before injecting.
//
//   zig cc -target x86_64-windows-gnu -shared -O2 -o bin/hook_guard.dll hook_guard.c

#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <wchar.h>

#define TARGET_RVA 0x1D3E290ULL
#define GUARD_VIOLATION 0x80000001L

static uintptr_t g_target = 0, g_page = 0;
static SIZE_T g_pagesize = 0x1000;
static volatile LONG g_hits = 0;
static wchar_t g_log[MAX_PATH];

static void logline(const char *fmt, ...) {
    char buf[512];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    HANDLE f = CreateFileW(g_log, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME t; GetLocalTime(&t);        // same clock as the server log, for lining them up
    char line[640];
    int n = snprintf(line, sizeof(line), "%02d:%02d:%02d.%03d %s\r\n", t.wHour, t.wMinute, t.wSecond,
                     t.wMilliseconds, buf);
    DWORD w; SetFilePointer(f, 0, NULL, FILE_END); WriteFile(f, line, (DWORD)n, &w, NULL); CloseHandle(f);
}

static LONG CALLBACK veh(PEXCEPTION_POINTERS ep) {
    if (ep->ExceptionRecord->ExceptionCode != GUARD_VIOLATION) return EXCEPTION_CONTINUE_SEARCH;
    CONTEXT *c = ep->ContextRecord;
    if (c->Rip == g_target) {
        // entering ssl_verify_cert_chain: return 1 to the caller without running it
        uintptr_t ret = *(uintptr_t *)c->Rsp;
        c->Rsp += 8; c->Rip = ret; c->Rax = 1;
        LONG h = InterlockedIncrement(&g_hits);
        if (h <= 3 || (h % 50) == 0) logline("hook hit #%ld -> return 1", h);
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    // any other access to the page (incl. VMProtect's integrity read): the guard is already cleared, so
    // the access proceeds; armer.exe re-arms it right away
    return EXCEPTION_CONTINUE_EXECUTION;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    (void)r;
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(h);
    // log file = this DLL's path with .log instead of .dll
    DWORD n = GetModuleFileNameW(h, g_log, MAX_PATH);
    if (n > 4 && n < MAX_PATH) wcscpy(g_log + n - 4, L".log");
    SYSTEM_INFO si; GetSystemInfo(&si); g_pagesize = si.dwPageSize;
    uintptr_t base = (uintptr_t)GetModuleHandleW(NULL);
    g_target = base + TARGET_RVA;
    g_page = g_target & ~((uintptr_t)g_pagesize - 1);
    AddVectoredExceptionHandler(1, veh);
    DWORD old;
    VirtualProtect((void *)g_page, g_pagesize, PAGE_EXECUTE_READ | PAGE_GUARD, &old);
    logline("attach: base=%p target=%p pid=%lu", (void *)base, (void *)g_target, GetCurrentProcessId());
    return TRUE;
}
