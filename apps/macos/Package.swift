// swift-tools-version: 6.1
import PackageDescription
let package = Package(name: "AIAnti", platforms: [.macOS(.v13)], products: [.executable(name: "AIAnti", targets: ["AIAnti"])], targets: [.executableTarget(name: "AIAnti", path: "Sources/AIReviewBar")], swiftLanguageModes: [.v5])
