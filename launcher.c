/* Tiny arm64 launcher for Jarvis.app.
 *
 * A compiled Mach-O executable (not a shell script) so macOS launches it as
 * native arm64 — no Rosetta/Intel fallback — and correctly attributes the
 * microphone permission to the app bundle. It just execs the venv Python.
 */
#include <unistd.h>

int main(void) {
    execl("/bin/bash", "bash", "-lc",
          "cd /PATH/TO/jarvis && "
          "mkdir -p data && "
          "exec ./.venv/bin/python -u -m jarvis --app >> data/app.log 2>&1",
          (char *)0);
    return 1; /* only reached if exec fails */
}
