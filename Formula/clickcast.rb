class Clickcast < Formula
  include Language::Python::Virtualenv

  desc "Drive a browser through a website and hand back a reel + AI-readable feedback sidecar"
  homepage "https://github.com/AlexKay28/clickcast"
  url "https://files.pythonhosted.org/packages/05/6e/cd75988e8976c333499c33f4b46114c8d77689a0b682709c925dc4bd7d4e/clickcast-0.2.9.tar.gz"
  sha256 "74559e28fcbe4c7369baaeecaa5bc8c119854ba7f8066880958edcce47b1e74b"
  license "MIT"

  depends_on "python@3.12"

  # Deliberately no system ffmpeg dependency line here: clickcast bundles one
  # via the Python `imageio[ffmpeg]` dependency already pulled in below.
  # Declaring a system ffmpeg here would put a second copy on disk for no
  # benefit -- see docs/packaging/homebrew.md.
  #
  # No Chromium here either -- it's a ~200MB download that changes on its own
  # release cadence, independent of clickcast's. Post-install `caveats` below
  # points at the existing `clickcast install` command instead.

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install "clickcast==#{version}"

    bin.install_symlink libexec/"bin/clickcast"
  end

  def caveats
    <<~EOS
      clickcast needs a Chromium browser (~180MB) that this formula does not
      bundle -- browsers are versioned independently of clickcast releases.
      Install it once:

        clickcast install --with-deps chromium

      ffmpeg is bundled via the Python `imageio[ffmpeg]` dependency; no
      system ffmpeg package is installed or required by this formula.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/clickcast --version").strip
  end
end
