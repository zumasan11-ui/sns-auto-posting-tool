import AppKit
import Foundation
import Vision

if CommandLine.arguments.count < 2 {
    fputs("usage: vision_ocr <image-path>\n", stderr)
    exit(2)
}

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path) else {
    fputs("failed to load image: \(path)\n", stderr)
    exit(1)
}

var rect = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
    fputs("failed to create CGImage: \(path)\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["ja-JP", "en-US"]
if #available(macOS 13.0, *) {
    request.revision = VNRecognizeTextRequestRevision3
}

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    fputs("ocr failed: \(error)\n", stderr)
    exit(1)
}

let lines = (request.results ?? [])
    .compactMap { $0.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines) }
    .filter { !$0.isEmpty }

print(lines.joined(separator: "\n"))
