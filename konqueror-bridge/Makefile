# Copyright 2026 Petri Koistinen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

CXX ?= c++
PREFIX ?= /usr/local
CXXFLAGS ?= -O2 -Wall -Wextra
QT6_INCS ?= -I$(PREFIX)/include/qt6 -I$(PREFIX)/include/qt6/QtCore -I$(PREFIX)/include/qt6/QtGui -I$(PREFIX)/include/qt6/QtWidgets
QT6_LIBS ?= -L$(PREFIX)/lib/qt6 -lQt6Core -lQt6Gui -lQt6Widgets

all: refineid-mtls-proxy refineid-ask-pin

refineid-mtls-proxy: refineid_mtls_proxy.cpp
	$(CXX) -std=c++17 $(CXXFLAGS) refineid_mtls_proxy.cpp -o $@ -lssl -lcrypto -lpthread

refineid-ask-pin: refineid_ask_pin.cpp
	$(CXX) -std=c++17 $(CXXFLAGS) -fPIC $(QT6_INCS) refineid_ask_pin.cpp -o $@ $(QT6_LIBS)

install: all
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(PREFIX)/etc/refineid $(DESTDIR)$(PREFIX)/etc/xdg/autostart
	install -m 755 refineid-mtls-proxy refineid-ask-pin $(DESTDIR)$(PREFIX)/bin/
	install -m 644 openssl_pkcs11.cnf $(DESTDIR)$(PREFIX)/etc/refineid/
	install -m 644 refineid-mtls-proxy.desktop $(DESTDIR)$(PREFIX)/etc/xdg/autostart/

clean:
	rm -f refineid-mtls-proxy refineid-ask-pin

.PHONY: all install clean
