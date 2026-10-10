// swift-tools-version: 6.1
import PackageDescription
let package = Package(name: "AIReviewBar", platforms: [.macOS(.v13)], products: [.executable(name: "AIReviewBar", targets: ["AIReviewBar"])], targets: [.executableTarget(name: "AIReviewBar")], swiftLanguageModes: [.v5])
