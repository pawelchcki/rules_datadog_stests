# Normalize Ruby paths before loading any native standard-library gems.
# Tree-artifact runfiles may contain individual symlinks rather than one
# directory alias. Ruby require_relative resolves their targets, while
# require uses the load path. Keep both on the same physical paths.
rbconfig = $LOADED_FEATURES.find { |path| path.end_with?("/rbconfig.rb") }
ruby_lib = File.realpath(rbconfig).split("/lib/ruby/", 2).first + "/lib/ruby/"
$LOAD_PATH.map! { |path| path.include?("/usr/local/lib/ruby/") ? ruby_lib + path.split("/usr/local/lib/ruby/", 2).last : path }
$LOADED_FEATURES.map! { |path| File.file?(path) && !path.end_with?(".so") ? File.realpath(path) : path }

# RubyGems also computes load paths from specification locations.
Gem::Specification.each do |spec|
  spec.loaded_from = File.realpath(spec.loaded_from) if !spec.default_gem? && spec.loaded_from && File.file?(spec.loaded_from)
end
