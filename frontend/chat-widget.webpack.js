/**
 * Webpack config to build the embeddable chat widget bundle.
 * Output: build/chat-widget.js  (single file, no chunk splitting)
 *
 * Run:  node chat-widget.webpack.js
 *  or:  npm run build:chat-widget
 */
const path    = require('path');
const webpack = require('webpack');

const isProd = process.env.NODE_ENV === 'production';

const config = {
  mode:  isProd ? 'production' : 'development',
  entry: './src/chat-widget.js',
  output: {
    path:     path.resolve(__dirname, 'build'),
    filename: 'chat-widget.js',
  },
  module: {
    rules: [
      {
        test:    /\.(js|jsx)$/,
        exclude: /node_modules/,
        use: {
          loader:  'babel-loader',
          options: { presets: ['@babel/preset-env', '@babel/preset-react'] }
        }
      },
      {
        test: /\.css$/,
        use:  ['style-loader', 'css-loader']
      },
      {
        test: /\.(png|svg|jpg|gif|woff2?|ttf|eot)$/,
        type: 'asset/inline'
      }
    ]
  },
  plugins: [
    new webpack.DefinePlugin({
      'process.env.NODE_ENV':          JSON.stringify(isProd ? 'production' : 'development'),
      'process.env.REACT_APP_API_URL': JSON.stringify(process.env.REACT_APP_API_URL || ''),
      'process.env.REACT_APP_WS_URL':  JSON.stringify(process.env.REACT_APP_WS_URL  || ''),
    })
  ],
  resolve: { extensions: ['.js', '.jsx'] },
  optimization: { splitChunks: false },
};

if (require.main === module) {
  const compiler = webpack(config);
  compiler.run((err, stats) => {
    if (err) { console.error(err); process.exit(1); }
    console.log(stats.toString({ colors: true, chunks: false }));
    if (stats.hasErrors()) process.exit(1);
    console.log('\nChat widget built: build/chat-widget.js');
    console.log('Customers embed it with:');
    console.log(`
<script
  src="https://yourplatform.com/chat-widget.js?v=1.0.0"
  data-api-key="va_..."
  data-agent-name="My Assistant"
  data-api-url="https://api.yourplatform.com"
></script>`);
  });
}

module.exports = config;
