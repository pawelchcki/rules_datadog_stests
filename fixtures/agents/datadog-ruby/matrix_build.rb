# Build metadata with the target Ruby, then load the actual tracing stack.
require File.join(ARGV.first, "paths.rb")
require "rubygems/package"
require "zlib"
require "fileutils"
require "json"
require "rbconfig"

root = ARGV.shift
msgpack = ARGV.shift
while !ARGV.empty?
  archive = ARGV.shift
  # Metadata comes from the checksum-verified gem repository. Avoid shipping
  # each complete source archive into every runtime's assembly action.
  spec = Zlib::GzipReader.open(archive) { |metadata| Gem::Specification.from_yaml(metadata.read) }
  abort("unsupported Ruby for #{spec.full_name}") unless spec.required_ruby_version.satisfied_by?(Gem::Version.new(RUBY_VERSION))
  source = File.join(root, "packages", spec.name.tr("-", "_"))
  destination = File.join(root, "gems", spec.full_name)
  FileUtils.mkdir_p(File.dirname(destination))
  FileUtils.cp_r(source, destination)
  if spec.name == "msgpack"
    FileUtils.mkdir_p(File.join(destination, "lib/msgpack"))
    FileUtils.cp(msgpack, File.join(destination, "lib/msgpack/msgpack.so"))
  end
  path = File.join(root, "specifications", spec.full_name + ".gemspec")
  FileUtils.mkdir_p(File.dirname(path))
  File.write(path, spec.to_ruby)
  spec.loaded_from = path
  # Datadog officially permits DD_NO_EXTENSION tracing installs. These
  # payloads contain native MessagePack; optional product extensions are
  # deliberately absent. Preserve the upstream specifications unchanged.
  unless spec.extensions.empty?
    FileUtils.mkdir_p(spec.extension_dir)
    File.write(File.join(spec.extension_dir, "gem.build_complete"), "")
  end
end
File.write(File.join(root, "abi.json"), JSON.generate({"ruby_version" => RbConfig::CONFIG.fetch("ruby_version"), "arch" => RbConfig::CONFIG.fetch("arch")}))
ENV["RULES_STESTS_DATADOG_RUBY_ROOT"] = root
require File.join(root, "activation.rb")
abort("wrong tracer") unless Gem.loaded_specs.fetch("datadog").version.to_s == "2.43.0"
value = {"message" => "zażółć-日本語", "integer" => 18446744073709551615}
abort("MessagePack ABI contract failed") unless MessagePack.unpack(MessagePack.pack(value)) == value
Datadog::Tracing.trace("matrix.build.contract") { |span| span.set_tag("ruby.version", RUBY_VERSION) }
Datadog.shutdown!
