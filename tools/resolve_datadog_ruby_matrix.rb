#!/usr/bin/env ruby
# Resolve the bounded native-runtime graph with RubyGems' own requirement rules.
require 'json'
require 'net/http'
require 'digest'
require 'rubygems'

MAX_JSON = 2 * 1024 * 1024
MAX_GEM = 128 * 1024 * 1024
VERSION = /\A[0-9]+(?:\.[0-9]+){2,5}\z/
NAMES = %w[datadog cgi datadog-ruby_core_source libdatadog libddwaf logger msgpack ffi].freeze
RUBIES = %w[2.5.9 2.6.10 2.7.8 3.0.7 3.1.7 3.2.11 3.3.12 3.4.8 4.0.1].freeze

def response(path, limit)
  uri = URI('https://rubygems.org' + path)
  result = nil
  Net::HTTP.start(uri.host, uri.port, use_ssl: true, open_timeout: 10, read_timeout: 30) do |http|
    http.request(Net::HTTP::Get.new(uri)) do |reply|
      raise 'registry response' unless reply.code == '200'
      size = 0
      reply.read_body do |chunk|
        size += chunk.bytesize
        raise 'registry size' if size > limit
        result = yield(chunk, result)
      end
    end
  end
  result
end

def metadata(path)
  JSON.parse(response(path, MAX_JSON) { |chunk, old| (old || +'') << chunk })
end

def details(name, version, platform)
  raise 'unsupported gem' unless NAMES.include?(name) && VERSION.match?(version)
  query = '?platform=' + platform
  value = metadata("/api/v2/rubygems/#{name}/versions/#{version}.json#{query}")
  raise 'gem identity' unless value.values_at('name', 'version', 'platform') == [name, version, platform] && !value['yanked']
  value
end

def requirement(text)
  Gem::Requirement.new(text.split(',').map(&:strip))
end

def compatible?(value, demands)
  demands.all? { |r| requirement(r).satisfied_by?(Gem::Version.new(value.fetch('version'))) } &&
    RUBIES.all? { |v| requirement(value.fetch('ruby_version')).satisfied_by?(Gem::Version.new(v)) }
end

begin
  version, lock_path = ARGV
  raise 'version' unless version && /\A[0-9]+\.[0-9]+\.[0-9]+\z/.match?(version)
  original = JSON.parse(File.read(lock_path))
  pins = original.fetch('gems').to_h { |g| [g.fetch('name'), g] }
  raise 'unsupported graph' unless pins.keys.sort == NAMES.sort
  selected = pins.to_h { |name, pin| [name, details(name, name == 'datadog' ? version : pin.fetch('version'), pin.fetch('platform', 'ruby'))] }
  finished = false
  16.times do
    demands = NAMES.to_h { |name| [name, []] }
    demands['datadog'] << '= ' + version
    selected.each_value do |value|
      value.fetch('dependencies').fetch('runtime').each do |dependency|
        raise 'unsupported graph' unless demands.key?(dependency.fetch('name'))
        demands[dependency['name']] << dependency.fetch('requirements')
      end
    end
    changed = false
    NAMES.each do |name|
      next if compatible?(selected.fetch(name), demands.fetch(name))
      raise 'unsupported Ruby range' if name == 'datadog'
      platform = pins.fetch(name).fetch('platform', 'ruby')
      candidates = metadata("/api/v1/versions/#{name}.json").select do |v|
        v['platform'] == platform && !v['prerelease'] && VERSION.match?(v.fetch('number')) &&
          demands[name].all? { |r| requirement(r).satisfied_by?(Gem::Version.new(v['number'])) }
      end.sort_by { |v| Gem::Version.new(v['number']) }.reverse
      choice = nil
      candidates.first(64).each do |v|
        candidate = details(name, v['number'], platform)
        if compatible?(candidate, demands[name])
          choice = candidate
          break
        end
      end
      raise 'no compatible native runtime dependency' unless choice
      changed ||= selected[name] != choice
      selected[name] = choice
    end
    unless changed
      finished = true
      break
    end
  end
  raise 'unresolved dependency graph' unless finished
  # The Bazel runtime admission table currently models this exact supported range.
  # A changed range requires a reviewed admission update, never guessed coverage.
  ruby_requirement = selected.fetch('datadog').fetch('ruby_version')
  raise 'unsupported Ruby range' unless ruby_requirement == '>= 2.5.0, < 4.1'
  gems = NAMES.map do |name|
    value = selected.fetch(name)
    platform = value.fetch('platform')
    filename = name + '-' + value.fetch('version') + (platform == 'ruby' ? '' : '-' + platform) + '.gem'
    digest = response('/downloads/' + filename, MAX_GEM) { |chunk, old| (old || Digest::SHA256.new).update(chunk) }.hexdigest
    raise 'gem checksum' unless value['sha'] == digest
    result = {'name' => name, 'version' => value['version'], 'sha256' => digest,
              'rubyRequirement' => value.fetch('ruby_version'), 'dependencies' => value.fetch('dependencies').fetch('runtime')}
    result['platform'] = platform unless platform == 'ruby'
    result
  end
  File.write(lock_path, JSON.pretty_generate({'schemaVersion' => 1, 'tracerVersion' => version,
    'rubyRequirement' => ruby_requirement, 'gems' => gems}) + "\n")
rescue StandardError => error
  location = error.backtrace_locations.find { |entry| entry.path == __FILE__ }
  warn "Ruby resolver diagnostic: #{error.class.name}, line #{location&.lineno}"
  warn 'Ruby matrix resolution failed; lock not accepted'
  exit 1
end
