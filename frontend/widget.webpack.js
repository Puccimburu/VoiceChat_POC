/**
 * Webpack config to build the embeddable widget bundle.
 * Output: build/widget.js  (single file, no chunk splitting)
 *
 * Run:  node widget.webpack.js
 *  or:  npm run build:widget
 */
const path    = require('path');
const webpack = require('webpack');

const isProd = process.env.NODE_ENV === 'production';

const config = {
  mode: isProd ? 'production' : 'development',
  entry: './src/widget.js',
  output: {
    path:     path.resolve(__dirname, 'build'),
    filename: 'widget.js',
  },
  module: {
    rules: [
      {
        test:    /\.(js|jsx)$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: ['@babel/preset-env', '@babel/preset-react']
          }
        }
      },
      {
        // Inline CSS so the widget is truly one file
        test: /\.css$/,
        use:  ['style-loader', 'css-loader']
      },
      {
        test: /\.(png|svg|jpg|gif|woff2?|ttf|eot)$/,
        type: 'asset/inline'   // base64 inline — no separate asset files
      }
    ]
  },
  plugins: [
    new webpack.DefinePlugin({
      'process.env.NODE_ENV':         JSON.stringify(isProd ? 'production' : 'development'),
      'process.env.REACT_APP_WS_URL': JSON.stringify(process.env.REACT_APP_WS_URL || ''),
    })
  ],
  resolve: {
    extensions: ['.js', '.jsx']
  },
  // Don't split chunks — customers need a single file
  optimization: {
    splitChunks: false,
  }
};

// If run directly (not required), execute webpack
if (require.main === module) {
  const compiler = webpack(config);
  compiler.run((err, stats) => {
    if (err) { console.error(err); process.exit(1); }
    console.log(stats.toString({ colors: true, chunks: false }));
    if (stats.hasErrors()) process.exit(1);
    console.log('\nWidget built: build/widget.js');
    console.log('Serve it and customers can embed it with:');
    console.log(`
<div id="voice-agent"></div>
<script
  src="https://yourplatform.com/widget.js"
  data-api-key="va_..."
  data-agent-name="My Assistant"
  data-target="voice-agent"
></script>`);
  });
}

module.exports = config;
