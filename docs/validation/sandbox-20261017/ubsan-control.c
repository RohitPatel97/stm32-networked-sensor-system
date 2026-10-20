#include <limits.h>
int main(void) { volatile int a = INT_MAX; volatile int b = a + 1; (void)b; return 0; }
