// dplist: list the DATA-PROTECTION keychain store from the shell.
//
// The plain kcdump query cannot see this store at all (errSecMissingEntitlement
// without a team signature). A bare binary cannot carry the needed
// keychain-access-groups entitlement either: AMFI SIGKILLs it at launch
// (Unsatisfied Entitlements). What works is packaging the tool as an .app:
//
//   swiftc -O -o dplist.app/Contents/MacOS/dplist dplist.swift -framework Security
//   codesign -s "Apple Development: PETRI TAPIO KOISTINEN (8CMQ8J6YPF)" \
//     --entitlements dplist.entitlements -f dplist.app
//   ./dplist.app/Contents/MacOS/dplist [group ...]
//
// With no group arguments, lists the ungrouped store plus the two known
// ReFineID groups. Secrets are never fetched: attributes enumerate silently,
// secret bytes would prompt per item.
import Foundation
import Security

func list(label: String, accessGroup: String?) {
  var query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecMatchLimit as String: kSecMatchLimitAll,
    kSecReturnAttributes as String: true,
    kSecUseDataProtectionKeychain as String: true,
  ]
  if let accessGroup {
    query[kSecAttrAccessGroup as String] = accessGroup
  }
  var result: CFTypeRef?
  let status = SecItemCopyMatching(query as CFDictionary, &result)
  guard status == errSecSuccess, let items = result as? [[String: Any]] else {
    print("\(label): status=\(status)")
    return
  }
  print("\(label): \(items.count) items")
  for item in items {
    let group = item[kSecAttrAccessGroup as String] ?? "?"
    let service = item[kSecAttrService as String] ?? "?"
    let account = item[kSecAttrAccount as String] ?? "?"
    print("[dp] agrp=\(group) service=\(service) account=\(account)")
  }
}

let groups =
  CommandLine.arguments.dropFirst().isEmpty
  ? [
    "4ZJC3SFJR2.fi.refineid.ReFineID",
    "4ZJC3SFJR2.fi.refineid.internal",
  ] : Array(CommandLine.arguments.dropFirst())
list(label: "no-group", accessGroup: nil)
for group in groups {
  list(label: "grouped (\(group))", accessGroup: group)
}
