/* SPDX-License-Identifier: GPL-3.0-or-later */
/* AI-Melee.exe: the thing to double-click in the Windows download. Opens the
 * AI-Melee launcher (ai-melee\launcher.py, a Tk window) with the windowless
 * Python: the py launcher's pyw.exe, else pythonw.exe on the PATH. No console
 * window, and a message box pointing at python.org when there's no Python.
 *
 * Built by tools/agent/package_windows_agent.sh with MinGW:
 *   x86_64-w64-mingw32-windres ai_melee_exe.rc -O coff -o icon.o
 *   x86_64-w64-mingw32-gcc -O2 -municode -mwindows ai_melee_exe.c icon.o -o AI-Melee.exe
 *
 * AI_MELEE_EXE_DRYRUN=1 (for tests) writes what it would run, or "no python",
 * to ai-melee-exe-dryrun.txt next to it instead of running or asking. */
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <wchar.h>

#define PYTHON_URL L"https://www.python.org/downloads/windows/"

static void dryrun_note(const wchar_t* dir, const wchar_t* text) {
    wchar_t path[MAX_PATH * 2];
    swprintf(path, sizeof path / sizeof path[0], L"%ls\\ai-melee-exe-dryrun.txt", dir);
    FILE* f = _wfopen(path, L"w, ccs=UTF-8");
    if (f != NULL) {
        fputws(text, f);
        fclose(f);
    }
}

static int exists(const wchar_t* path) {
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

/* Where python.org's installers put the py launcher, then the PATH. Never the current
 * directory: whatever folder the exe was started from mustn't supply its "Python". */
static int find_python(wchar_t* out, DWORD cap) {
    static const wchar_t* const launchers[] = {L"%LOCALAPPDATA%\\Programs\\Python\\Launcher\\pyw.exe",
                                               L"%WINDIR%\\pyw.exe"};
    for (int i = 0; i < 2; ++i) {
        DWORD n = ExpandEnvironmentStringsW(launchers[i], out, cap);
        if (n > 0 && n <= cap && exists(out)) {
            return 1;
        }
    }
    static wchar_t path_env[32767];
    DWORD n = GetEnvironmentVariableW(L"PATH", path_env, sizeof path_env / sizeof path_env[0]);
    if (n == 0 || n >= sizeof path_env / sizeof path_env[0]) {
        return 0;
    }
    static const wchar_t* const names[] = {L"pyw.exe", L"pythonw.exe"};
    for (int i = 0; i < 2; ++i) {
        if (SearchPathW(path_env, names[i], NULL, cap, out, NULL) > 0) {
            return 1;
        }
    }
    return 0;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show) {
    (void)inst;
    (void)prev;
    (void)args;
    (void)show;
    wchar_t dir[MAX_PATH];
    DWORD n = GetModuleFileNameW(NULL, dir, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) {
        return 1;
    }
    wchar_t* slash = wcsrchr(dir, L'\\');
    if (slash != NULL) {
        *slash = L'\0';
    }
    const int dryrun = _wgetenv(L"AI_MELEE_EXE_DRYRUN") != NULL;

    wchar_t script[MAX_PATH * 2], workdir[MAX_PATH * 2];
    swprintf(script, sizeof script / sizeof script[0], L"%ls\\ai-melee\\launcher.py", dir);
    swprintf(workdir, sizeof workdir / sizeof workdir[0], L"%ls\\ai-melee", dir);
    if (GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        if (dryrun) {
            dryrun_note(dir, L"no launcher");
        } else {
            MessageBoxW(NULL,
                L"Can't find ai-melee\\launcher.py.\n\nKeep AI-Melee.exe in the AI-Melee folder, next to "
                L"the ai-melee and game folders (extract the whole zip).",
                L"AI-Melee", MB_OK | MB_ICONERROR);
        }
        return 1;
    }

    wchar_t python[MAX_PATH];
    if (!find_python(python, MAX_PATH)) {
        if (dryrun) {
            dryrun_note(dir, L"no python");
        } else if (MessageBoxW(NULL,
                       L"AI-Melee needs Python 3.12 from python.org.\n\nIn the installer, tick \"Add "
                       L"python.exe to PATH\", then open AI-Melee again.\n\nOpen the download page now?",
                       L"AI-Melee", MB_YESNO | MB_ICONERROR) == IDYES) {
            ShellExecuteW(NULL, L"open", PYTHON_URL, NULL, NULL, SW_SHOWNORMAL);
        }
        return 1;
    }

    wchar_t cmd[MAX_PATH * 5];
    swprintf(cmd, sizeof cmd / sizeof cmd[0], L"\"%ls\" \"%ls\"", python, script);
    if (dryrun) {
        dryrun_note(dir, cmd);
        return 0;
    }
    STARTUPINFOW si = {.cb = sizeof si};
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, 0, NULL, workdir, &si, &pi)) {
        wchar_t msg[MAX_PATH * 2];
        swprintf(msg, sizeof msg / sizeof msg[0], L"Couldn't start %ls (error %lu).", python, GetLastError());
        MessageBoxW(NULL, msg, L"AI-Melee", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
