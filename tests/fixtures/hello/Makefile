VERSION ?= unknown
all: apt-repo-selftest-hello
apt-repo-selftest-hello: hello.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -DVERSION='"$(VERSION)"' $(LDFLAGS) -o $@ $<
install: apt-repo-selftest-hello
	install -D -m 755 apt-repo-selftest-hello $(DESTDIR)/usr/bin/apt-repo-selftest-hello
clean:
	rm -f apt-repo-selftest-hello
